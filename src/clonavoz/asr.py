"""Reconocimiento de voz (ASR) en streaming usando faster-whisper (Whisper
optimizado con CTranslate2), que corre bien en CPU y soporta unos 99 idiomas.
"""
from __future__ import annotations

import numpy as np
from faster_whisper import WhisperModel

from .config import PerformanceProfile


class SpeechRecognizer:
    def __init__(self, profile: PerformanceProfile) -> None:
        device = profile.device if profile.device in ("cuda", "cpu") else "cpu"
        self._model = WhisperModel(
            profile.whisper_model,
            device=device,
            compute_type=profile.whisper_compute_type,
        )

    def transcribe(self, audio: np.ndarray, whisper_language: str) -> str:
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
