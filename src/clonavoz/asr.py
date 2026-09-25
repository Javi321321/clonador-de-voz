"""Reconocimiento de voz (ASR). Con Parakeet (ver `parakeet_asr.py`) si está
descargado, entiende tu idioma y la PC tiene memoria suficiente: se equivoca
bastante menos. Si no, faster-whisper (Whisper optimizado con CTranslate2),
que corre bien en CPU y entiende unos 99 idiomas.
"""
from __future__ import annotations

import numpy as np
import psutil

# En Windows, torch y ctranslate2 traen cada uno su copia de libiomp5md.dll
# (OpenMP): cargando torch primero, ctranslate2 reutiliza esa misma copia en vez
# de cargar una segunda, que cerraría el programa con "OMP: Error #15".
import torch  # noqa: F401  (ver arriba: tiene que importarse antes que ctranslate2)
from faster_whisper import WhisperModel

from . import parakeet_asr
from .config import PerformanceProfile


class SpeechRecognizer:
    """`language_code`: el idioma en el que vas a hablar (código de clonavoz),
    para saber si Parakeet lo entiende. Sin él se usa Whisper."""

    def __init__(self, profile: PerformanceProfile, language_code: str | None = None) -> None:
        self._parakeet = None
        self._model = None
        if language_code is not None and parakeet_asr.usable(language_code):
            cores = psutil.cpu_count(logical=False) or 2
            self._parakeet = parakeet_asr.ParakeetRecognizer(threads=max(1, min(4, cores)))
            self.name = "Parakeet"
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

    def transcribe_timed(self, audio: np.ndarray) -> tuple[list[str], list[float]]:
        return self._parakeet.transcribe_timed(audio)

    def transcribe(self, audio: np.ndarray, whisper_language: str) -> str:
        if self._parakeet is not None:
            return self._parakeet.transcribe(audio)
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
