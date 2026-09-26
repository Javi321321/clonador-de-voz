"""Reconocimiento de voz (ASR). Con Parakeet (ver `parakeet_asr.py`) si está
descargado, entiende tu idioma y la PC tiene memoria suficiente: se equivoca
bastante menos. Si no, Vosk (ver `vosk_asr.py`), que va entendiendo mientras
hablás, si está descargado para tu idioma. Si no, faster-whisper (Whisper
optimizado con CTranslate2), que corre bien en CPU y entiende unos 99 idiomas.
"""
from __future__ import annotations

import zlib

import numpy as np
import psutil

# En Windows, torch y ctranslate2 traen cada uno su copia de libiomp5md.dll
# (OpenMP): cargando torch primero, ctranslate2 reutiliza esa misma copia en vez
# de cargar una segunda, que cerraría el programa con "OMP: Error #15".
import torch  # noqa: F401  (ver arriba: tiene que importarse antes que ctranslate2)
from faster_whisper import WhisperModel
from faster_whisper.tokenizer import Tokenizer

from . import parakeet_asr, vosk_asr
from .config import PerformanceProfile

# Whisper mira siempre una ventana de 30 s de audio (3000 cuadros de 10 ms; lo
# que falta lo rellena con silencio), y eso es lo que más tarda. Para frases de
# hasta 7 s alcanza con una de 10 s: el mismo texto hasta 4 veces más rápido,
# lo que en una PC lenta es la diferencia entre llegar o no a tiempo. (Con una
# de 5 s se equivocaba bastante más: 42% de palabras distintas contra 28%.)
# (cuadros de la frase como máximo, cuadros de la ventana): si una da un
# resultado dudoso, se prueba con la siguiente y, si no, la de 30 s.
_SHORT_WINDOWS = ((700, 1000),)


def _compression_ratio(text: str) -> float:
    """Alto si el texto se repite (así se nota que Whisper se trabó en un bucle)."""
    data = text.encode("utf-8")
    return len(data) / len(zlib.compress(data)) if data else 0.0


class SpeechRecognizer:
    """`language_code`: el idioma en el que vas a hablar (código de clonavoz),
    para saber si Parakeet o Vosk lo entienden. Sin él se usa Whisper.
    `parakeet=False`: sin Parakeet (en una PC donde tarda demasiado)."""

    def __init__(self, profile: PerformanceProfile, language_code: str | None = None, parakeet: bool = True) -> None:
        self._parakeet = None
        self._vosk = None
        self._model = None
        if language_code is not None and parakeet and parakeet_asr.usable(language_code):
            cores = psutil.cpu_count(logical=False) or 2
            self._parakeet = parakeet_asr.shared(threads=max(1, min(4, cores)))
            self.name = "Parakeet"
            return
        if language_code is not None and vosk_asr.ready(language_code):
            self._vosk = vosk_asr.VoskModel(language_code)
            self.name = "Vosk"
            return
        device = profile.device if profile.device in ("cuda", "cpu") else "cpu"
        self._model = WhisperModel(
            profile.whisper_model,
            device=device,
            compute_type=profile.whisper_compute_type,
        )
        self.name = f"Whisper {profile.whisper_model}"

    @property
    def can_split(self) -> bool:
        """Si da el tiempo de cada palabra (para la traducción simultánea)."""
        return self._parakeet is not None

    @property
    def streaming(self) -> bool:
        """Si entiende mientras hablás (Vosk): el texto está listo al terminar la
        frase. Sin puntuación: ver punctuation.py."""
        return self._vosk is not None

    def stream(self) -> vosk_asr.VoskStream:
        return self._vosk.stream()

    def transcribe_timed(self, audio: np.ndarray) -> tuple[list[str], list[float]]:
        return self._parakeet.transcribe_timed(audio)

    def _encode_short(self, audio: np.ndarray) -> tuple[object, int] | None:
        """(audio codificado por Whisper, cuadros) con una ventana corta, o None
        si la frase es larga para eso."""
        features = self._model.feature_extractor(np.asarray(audio, dtype=np.float32))
        frames = features.shape[-1]
        for max_frames, window_frames in _SHORT_WINDOWS:
            if frames <= max_frames:
                window = np.zeros((features.shape[0], window_frames), dtype=features.dtype)
                window[:, :frames] = features  # el resto, como el relleno que usa faster-whisper
                return self._model.encode(window), frames
        return None

    def _transcribe_short(self, audio: np.ndarray, whisper_language: str) -> str | None:
        """Una frase corta, con una ventana chica y una sola pasada por ventana.
        None si la frase es larga o ninguna ventana dio un resultado confiable
        (se repite o Whisper duda): ahí se usa el camino normal, con la ventana
        de 30 s y sus reintentos."""
        encoded = self._encode_short(audio)
        if encoded is None:
            return None
        return self._generate(*encoded, whisper_language)

    def _generate(self, encoded, frames: int, whisper_language: str) -> str | None:
        model = self._model
        tokenizer = Tokenizer(model.hf_tokenizer, model.model.is_multilingual, task="transcribe", language=whisper_language)
        prompt = model.get_prompt(tokenizer, [], without_timestamps=False)
        result = model.model.generate(
            encoded,
            [prompt],
            beam_size=1,
            max_length=len(prompt) + 20 + frames // 8,  # si se traba repitiendo, corta pronto
            return_scores=True,
            return_no_speech_prob=True,
        )[0]
        tokens = result.sequences_ids[0]
        text = tokenizer.decode(tokens).strip()
        avg_logprob = result.scores[0] * len(tokens) / (len(tokens) + 1)
        if result.no_speech_prob > 0.75 and avg_logprob < -1.0:
            return ""  # silencio, ruido o un golpe (como no_speech_threshold, ver abajo)
        if _compression_ratio(text) > 2.4 or avg_logprob < -1.0:
            return None
        return text

    def language_probabilities(self, audio: np.ndarray) -> tuple[dict[str, float], object]:
        """Qué tan probable es cada idioma (código de Whisper), según los primeros
        7 s. Devuelve también lo codificado, para transcribir después sin volver
        a codificar (con `transcribe_encoded`) si la frase es corta."""
        clip = np.asarray(audio, dtype=np.float32)[: 7 * 16000]
        encoded = self._encode_short(clip)
        result = self._model.model.detect_language(encoded[0])[0]
        probabilities = {token[2:-2]: prob for token, prob in result}
        return probabilities, (encoded if len(clip) == len(audio) else None)

    def transcribe_encoded(self, audio: np.ndarray, whisper_language: str, encoded=None) -> str:
        """Como `transcribe`, aprovechando lo que ya codificó `language_probabilities`."""
        if encoded is not None:
            try:
                text = self._generate(*encoded, whisper_language)
            except Exception:  # noqa: BLE001 - ante cualquier problema, el camino normal
                text = None
            if text is not None:
                return text
        return self.transcribe(audio, whisper_language)

    def transcribe(self, audio: np.ndarray, whisper_language: str) -> str:
        if self._parakeet is not None:
            return self._parakeet.transcribe(audio)
        if self._vosk is not None:
            return self._vosk.transcribe(audio)
        try:
            text = self._transcribe_short(audio, whisper_language)
        except Exception:  # noqa: BLE001 - ante cualquier problema, la ventana de siempre
            text = None
        if text is not None:
            return text
        segments, _info = self._model.transcribe(
            audio,
            language=whisper_language,
            beam_size=1,
            vad_filter=False,
            condition_on_previous_text=False,
            # Hasta 2 reintentos si Whisper duda (por defecto son 5, y en vivo
            # cada uno suma demora). Y un poco menos estricto para decidir
            # "esto no es voz" (0.6 por defecto), porque así se descartaban a
            # veces frases bien dichas; con 0.75 igual se descartan silencio,
            # ruido, golpes y zumbidos (dan 0.84 o más), que si no Whisper los
            # "transcribe" como frases inventadas.
            temperature=(0.0, 0.2, 0.4),
            no_speech_threshold=0.75,
        )
        return " ".join(segment.text.strip() for segment in segments).strip()
