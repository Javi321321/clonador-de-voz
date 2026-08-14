"""Síntesis de voz: clona tu timbre con XTTS-v2 en los ~17 idiomas que
soporta; para el resto usa una voz neutra de alta calidad (Piper) para que
el sistema funcione de extremo a extremo en cualquier idioma, aunque en
esos casos no pueda clonar tu voz todavía.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from .config import PerformanceProfile
from .languages import Language
from .piper_fallback import FallbackSynthesizer


class VoiceSynthesizer:
    def __init__(self, profile: PerformanceProfile, reference_wav: Path) -> None:
        if not reference_wav.exists():
            raise FileNotFoundError(
                f"No se encontró la muestra de voz de referencia: {reference_wav}\n"
                "Grábala primero con `clonavoz enroll`."
            )
        self.reference_wav = str(reference_wav)
        self._profile = profile
        self._xtts = None
        self._fallback: FallbackSynthesizer | None = None
        self._warned_languages: set[str] = set()

    def _get_xtts(self):
        if self._xtts is None:
            from TTS.api import TTS

            self._xtts = TTS(self._profile.tts_model)
            device = self._profile.device if self._profile.device == "cuda" else "cpu"
            self._xtts.to(device)
        return self._xtts

    def _get_fallback(self) -> FallbackSynthesizer:
        if self._fallback is None:
            self._fallback = FallbackSynthesizer()
        return self._fallback

    def synthesize(self, text: str, language: Language) -> tuple[np.ndarray, int]:
        if not text.strip():
            return np.array([], dtype=np.float32), 24000

        if language.xtts_code is not None:
            tts = self._get_xtts()
            wav = tts.tts(text=text, speaker_wav=self.reference_wav, language=language.xtts_code)
            sample_rate = tts.synthesizer.output_sample_rate
            return np.asarray(wav, dtype=np.float32), sample_rate

        if language.code not in self._warned_languages:
            print(
                f"[clonavoz] Aviso: la clonación de tu voz todavía no está disponible en "
                f"'{language.name}' (limitación de los modelos abiertos actuales). "
                "Se usará una voz neutra de alta calidad en su lugar."
            )
            self._warned_languages.add(language.code)

        pcm, sample_rate = self._get_fallback().synthesize(text, language.code)
        return pcm, sample_rate
