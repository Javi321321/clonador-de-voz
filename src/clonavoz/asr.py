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
        )
        return " ".join(segment.text.strip() for segment in segments).strip()
