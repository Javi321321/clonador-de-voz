"""Detección de actividad de voz en streaming (Silero VAD) para cortar el
audio del micrófono en frases habladas naturales apenas termina una pausa,
en lugar de esperar un buffer fijo largo. Esto es lo que mantiene la
latencia baja: cada frase se procesa tan pronto como se termina de decir.

Si hablás de corrido, sin hacer una pausa larga, la frase se corta en la
primera pausa corta (entre dos palabras, al respirar) después de
`max_utterance_seconds`, y no a la fuerza en ese segundo exacto: cortar en
medio de una palabra hacía que esa palabra se entendiera y tradujera mal.
Solo si no hay ninguna pausa se corta igual, más adelante.
"""
from __future__ import annotations

import warnings
from collections import deque

import numpy as np
import torch

_FRAME_SAMPLES = 512  # ventana que espera silero-vad a 16kHz (32 ms)
_FRAME_MS = _FRAME_SAMPLES * 1000 / 16000
_SILENCE_MS_TO_END = 500  # pausa que termina una frase
_SHORT_PAUSE_MS = 160  # pausa entre palabras donde se puede cortar una frase larga
_HARD_MAX_FACTOR = 2.5  # sin ninguna pausa, se corta en max_utterance_seconds * esto
_SPEECH = 0.5  # probabilidad de voz desde la que se considera que hablás
_NOT_SPEECH = 0.35  # y por debajo de la cual, que es silencio (como Silero)
_PREROLL_FRAMES = 2  # se guarda un poquito de antes, para no comerse el comienzo


def _load_silero():
    try:
        # El paquete de pip trae el modelo adentro: no necesita internet.
        import silero_vad

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
    un arreglo numpy con cada frase hablada completa apenas detecta una
    pausa (ver arriba cómo se cortan las frases largas).

    `model` es para las pruebas: cualquier cosa que, llamada con un bloque de
    audio, devuelva la probabilidad de que sea voz."""

    def __init__(self, max_utterance_seconds: float = 5.0, model=None) -> None:
        threads = torch.get_num_threads()
        self._model = model if model is not None else _load_silero()
        # silero-vad hace torch.set_num_threads(1) al importarse, y eso vale
        # para todo el proceso: la traducción y la voz quedarían usando un solo
        # núcleo y cada frase tardaría el doble o más en salir traducida.
        torch.set_num_threads(threads)
        self._soft_max = int(max_utterance_seconds * 16000)
        self._hard_max = int(max_utterance_seconds * _HARD_MAX_FACTOR * 16000)
        self._end_frames = int(np.ceil(_SILENCE_MS_TO_END / _FRAME_MS))
        self._pause_frames = int(np.ceil(_SHORT_PAUSE_MS / _FRAME_MS))
        self._preroll: deque[np.ndarray] = deque(maxlen=_PREROLL_FRAMES)
        self._buffer: list[np.ndarray] = []
        self._samples = 0
        self._silent_frames = 0
        self._speaking = False

    def _probability(self, frame: np.ndarray) -> float:
        with torch.no_grad():
            return float(self._model(torch.from_numpy(frame), 16000))

    def push(self, frame: np.ndarray) -> np.ndarray | None:
        prob = self._probability(frame)
        if not self._speaking:
            self._preroll.append(frame)
            if prob >= _SPEECH:
                self._speaking = True
                self._buffer = list(self._preroll)
                self._samples = sum(len(f) for f in self._buffer)
                self._preroll.clear()
                self._silent_frames = 0
            return None

        self._buffer.append(frame)
        self._samples += len(frame)
        if prob < _NOT_SPEECH:
            self._silent_frames += 1
        elif prob >= _SPEECH:
            self._silent_frames = 0

        if self._silent_frames >= self._end_frames:
            return self._flush()
        if self._samples >= self._soft_max and self._silent_frames >= self._pause_frames:
            return self._flush()
        if self._samples >= self._hard_max:
            return self._flush()
        return None

    def _flush(self) -> np.ndarray:
        # Del silencio del final se deja solo un poquito (a Whisper no le sirve más).
        keep = len(self._buffer) - max(0, self._silent_frames - 3)
        utterance = np.concatenate(self._buffer[:keep]) if self._buffer else np.array([], dtype=np.float32)
        self._speaking = False
        self._buffer = []
        self._samples = 0
        self._silent_frames = 0
        reset = getattr(self._model, "reset_states", None)
        if reset is not None:
            reset()
        return utterance

    @property
    def is_speaking(self) -> bool:
        """True mientras hay una frase en curso (se detectó voz y todavía no
        terminó la pausa)."""
        return self._speaking

    @staticmethod
    def frame_size() -> int:
        return _FRAME_SAMPLES
