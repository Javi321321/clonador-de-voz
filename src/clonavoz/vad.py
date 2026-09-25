"""Detección de actividad de voz en streaming (Silero VAD) para cortar el
audio del micrófono en partes que se traducen apenas se terminan de decir.

Sin ayuda, una frase termina con una pausa (`_SILENCE_MS_TO_END`). Si hablás
de corrido, se corta en la primera pausa corta (entre dos palabras, al
respirar) después de `max_utterance_seconds`, y si no hay ninguna pausa, más
adelante en el momento más silencioso, nunca a la fuerza en medio de una
palabra (eso hacía que se entendiera y tradujera mal).

Con un `splitter` (ver `simultaneous.py`), además, mientras hablás se va
reconociendo lo que decís y se corta apenas termina una idea (una coma, un
punto), para traducirla sin esperar al final: como un intérprete simultáneo.
"""
from __future__ import annotations

import warnings
from collections import deque
from dataclasses import dataclass

import numpy as np
import torch

_FRAME_SAMPLES = 512  # ventana que espera silero-vad a 16kHz (32 ms)
_FRAME_MS = _FRAME_SAMPLES * 1000 / 16000
_SILENCE_MS_TO_END = 500  # pausa que termina una frase
_EARLY_END_MS = 250  # con esta pausa se pregunta si la oración ya terminó (ej. terminó en punto)
_SHORT_PAUSE_MS = 160  # pausa entre palabras donde se puede cortar una frase larga
_HARD_MAX_FACTOR = 2.5  # sin ninguna pausa, se corta en max_utterance_seconds * esto
_SPEECH = 0.5  # probabilidad de voz desde la que se considera que hablás
_NOT_SPEECH = 0.35  # y por debajo de la cual, que es silencio (como Silero)
_PREROLL_FRAMES = 2  # se guarda un poquito de antes, para no comerse el comienzo
_SPLIT_EVERY_MS = 700  # cada cuánto se consulta al splitter mientras hablás
_SPLIT_MIN_MS = 2000  # y desde cuánto audio (las frases cortas terminan con su pausa)


@dataclass
class Utterance:
    audio: np.ndarray
    text: str | None = None  # si ya se reconoció al cortarla (con el splitter)


def _load_silero():
    try:
        # El paquete de pip trae el modelo adentro: no necesita internet.
        import silero_vad

        try:
            # La versión ONNX es el doble de rápida y, sobre todo, no compite
            # por los hilos de PyTorch con la voz natural, que se genera a la vez.
            return silero_vad.load_silero_vad(onnx=True)
        except Exception:  # noqa: BLE001 - sin onnxruntime: la versión de PyTorch
            with warnings.catch_warnings():
                # Aviso interno de PyTorch sobre cómo está guardado el modelo:
                # no afecta en nada y confundía al aparecer en cada arranque.
                warnings.filterwarnings("ignore", message=".*torch.jit.load", category=FutureWarning)
                return silero_vad.load_silero_vad(onnx=False)
    except ImportError:
        model, _utils = torch.hub.load(
            repo_or_dir="snakers4/silero-vad",
            model="silero_vad",
            trust_repo=True,
            onnx=False,
        )
        return model


class StreamingVAD:
    """Recibe audio en bloques pequeños (`frame_size()` muestras) y libera
    cada parte hablada (`Utterance`) apenas corresponde cortarla.

    `splitter(audio, final)`: opcional; recibe el audio de la frase en curso y
    devuelve `(muestras, texto)` para cortarla ahí (ya reconocida) o None.
    Con `final=True` se le pregunta si la frase, que está en una pausa, ya
    terminó. `model` es para las pruebas: cualquier cosa que, llamada con un
    bloque de audio, devuelva la probabilidad de que sea voz."""

    def __init__(self, max_utterance_seconds: float = 5.0, model=None, splitter=None) -> None:
        threads = torch.get_num_threads()
        self._model = model if model is not None else _load_silero()
        # silero-vad hace torch.set_num_threads(1) al importarse, y eso vale
        # para todo el proceso: la traducción y la voz quedarían usando un solo
        # núcleo y cada frase tardaría el doble o más en salir traducida.
        torch.set_num_threads(threads)
        self._splitter = splitter
        self._soft_max = int(max_utterance_seconds * 16000)
        self._hard_max = int(max_utterance_seconds * _HARD_MAX_FACTOR * 16000)
        self._end_frames = int(np.ceil(_SILENCE_MS_TO_END / _FRAME_MS))
        self._early_end_frames = int(np.ceil(_EARLY_END_MS / _FRAME_MS))
        self._pause_frames = int(np.ceil(_SHORT_PAUSE_MS / _FRAME_MS))
        self._split_every = int(_SPLIT_EVERY_MS / _FRAME_MS)
        self._split_min = int(_SPLIT_MIN_MS * 16)
        self._preroll: deque[np.ndarray] = deque(maxlen=_PREROLL_FRAMES)
        self._reset()

    def _reset(self) -> None:
        self._buffer: list[np.ndarray] = []
        self._probs: list[float] = []
        self._samples = 0
        self._silent_frames = 0
        self._speaking = False
        self._since_split = 0
        self._asked_early_end = False

    def _probability(self, frame: np.ndarray) -> float:
        with torch.no_grad():
            return float(self._model(torch.from_numpy(frame), 16000))

    def push(self, frame: np.ndarray) -> Utterance | None:
        prob = self._probability(frame)
        if not self._speaking:
            self._preroll.append(frame)
            if prob >= _SPEECH:
                self._speaking = True
                self._buffer = list(self._preroll)
                self._probs = [1.0] * len(self._buffer)
                self._samples = sum(len(f) for f in self._buffer)
                self._preroll.clear()
                self._silent_frames = 0
            return None

        self._buffer.append(frame)
        self._probs.append(prob)
        self._samples += len(frame)
        self._since_split += 1
        if prob < _NOT_SPEECH:
            self._silent_frames += 1
        elif prob >= _SPEECH:
            self._silent_frames = 0
            self._asked_early_end = False

        if self._silent_frames >= self._end_frames:
            return self._flush()
        if self._splitter is not None:
            utterance = self._ask_splitter()
            if utterance is not None:
                return utterance
        if self._samples >= self._soft_max and self._silent_frames >= self._pause_frames:
            return self._flush()
        if self._samples >= self._hard_max:
            return self._cut_at_quietest()
        return None

    def _ask_splitter(self) -> Utterance | None:
        if self._silent_frames >= self._early_end_frames and not self._asked_early_end:
            # En una pausa: ¿la oración ya terminó? Así no se esperan los 500 ms.
            self._asked_early_end = True
            result = self._splitter(self._audio(), final=True)
            if result is not None and result[0] >= self._samples:
                return self._flush(text=result[1])
            return None
        if self._samples >= self._split_min and self._since_split >= self._split_every and self._silent_frames == 0:
            self._since_split = 0
            result = self._splitter(self._audio(), final=False)
            if result is not None:
                return self._cut(*result)
        return None

    def _audio(self) -> np.ndarray:
        return np.concatenate(self._buffer) if self._buffer else np.zeros(0, dtype=np.float32)

    def _cut(self, samples: int, text: str | None) -> Utterance:
        """Libera el audio hasta `samples` y sigue con el resto como frase en curso."""
        audio = self._audio()
        samples = max(0, min(samples, len(audio)))
        rest = audio[samples:]
        whole_frames = len(rest) // _FRAME_SAMPLES
        self._buffer = [rest[i * _FRAME_SAMPLES : (i + 1) * _FRAME_SAMPLES] for i in range(whole_frames)]
        if len(rest) % _FRAME_SAMPLES:
            self._buffer.append(rest[whole_frames * _FRAME_SAMPLES :])
        self._probs = self._probs[len(self._probs) - len(self._buffer) :] if self._buffer else []
        self._samples = len(rest)
        self._since_split = 0
        return Utterance(audio[:samples], text)

    def _cut_at_quietest(self) -> Utterance:
        """Sin ninguna pausa: se corta en el momento más silencioso del último
        segundo y medio, que casi siempre cae entre dos palabras."""
        window = min(len(self._probs) - 1, int(1500 / _FRAME_MS))
        start = len(self._probs) - window
        quietest = start + int(np.argmin(self._probs[start:]))
        samples = sum(len(f) for f in self._buffer[: quietest + 1])
        return self._cut(samples, None)

    def _flush(self, text: str | None = None) -> Utterance:
        # Del silencio del final se deja solo un poquito (a Whisper no le sirve más).
        keep = len(self._buffer) - max(0, self._silent_frames - 3)
        audio = np.concatenate(self._buffer[:keep]) if self._buffer else np.array([], dtype=np.float32)
        self._reset()
        reset = getattr(self._model, "reset_states", None)
        if reset is not None:
            reset()
        return Utterance(audio, text)

    @property
    def is_speaking(self) -> bool:
        """True mientras hay una frase en curso (se detectó voz y todavía no
        terminó la pausa)."""
        return self._speaking

    @staticmethod
    def frame_size() -> int:
        return _FRAME_SAMPLES
