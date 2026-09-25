"""Síntesis de voz con tu timbre. Dos motores:

- "openvoice" (por defecto sin GPU): Piper dice la frase en el idioma destino
  y el conversor de OpenVoice V2 le pone tu timbre. Unas 10 veces más rápido
  que XTTS-v2 en CPU, usa poca memoria y clona tu voz en todos los idiomas
  que tienen voz de Piper (~37). Con una voz base de tono parecido al tuyo,
  en nuestras pruebas se parece a la voz original tanto o más que XTTS-v2.
- "xtts" (por defecto con GPU NVIDIA): XTTS-v2 genera la voz clonada
  directamente, con una entonación algo más natural, pero es pesado: en CPU
  cada frase tarda varios segundos. Clona en ~17 idiomas; para el resto usa
  una voz neutra de Piper. Requiere `pip install "coqui-tts[codec]"`.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

from . import openvoice
from .config import PerformanceProfile
from .languages import Language
from .piper_tts import PiperSynthesizer, median_pitch

ENGINES = ("openvoice", "xtts")


def xtts_installed() -> bool:
    try:
        import TTS  # noqa: F401
    except ImportError:
        return False
    return True


class VoiceSynthesizer:
    def __init__(self, profile: PerformanceProfile, reference_wav: Path, engine: str = "openvoice") -> None:
        if not reference_wav.exists():
            raise FileNotFoundError(
                f"No se encontró la muestra de voz de referencia: {reference_wav}\n"
                "Grábala primero con `clonavoz enroll`."
            )
        if engine not in ENGINES:
            raise ValueError(f"Motor de voz desconocido: '{engine}'. Opciones: {', '.join(ENGINES)}")
        self.reference_wav = str(reference_wav)
        self.engine = engine
        self._profile = profile
        self._xtts = None
        self._piper: PiperSynthesizer | None = None
        self._converter: openvoice.ToneColorConverter | None = None
        self._target_embedding = None
        self._source_embeddings: dict[str, object] = {}  # por voz de Piper
        self._warned_languages: set[str] = set()

    def _reference_audio(self) -> tuple[np.ndarray, int]:
        audio, sample_rate = sf.read(self.reference_wav, dtype="float32")
        return (audio.mean(axis=1) if audio.ndim > 1 else audio), sample_rate

    def _get_xtts(self):
        if self._xtts is None:
            try:
                from TTS.api import TTS
            except ImportError as exc:
                raise RuntimeError(
                    'El motor de voz XTTS no está instalado. Instálalo con: pip install "coqui-tts[codec]"\n'
                    "(o usa el motor liviano: --voice-engine openvoice)."
                ) from exc

            self._xtts = TTS(self._profile.tts_model)
            device = self._profile.device if self._profile.device == "cuda" else "cpu"
            self._xtts.to(device)
        return self._xtts

    def _get_piper(self) -> PiperSynthesizer:
        if self._piper is None:
            # Con el tono de tu voz se elige la voz base (grave o aguda) de cada idioma.
            self._piper = PiperSynthesizer(speaker_pitch_hz=median_pitch(*self._reference_audio()))
        return self._piper

    def _get_converter(self) -> openvoice.ToneColorConverter:
        if self._converter is None:
            self._converter = openvoice.ToneColorConverter.from_pretrained()
            self._target_embedding = self._converter.embedding(*self._reference_audio())
        return self._converter

    def _source_embedding(self, language: Language):
        piper = self._get_piper()
        voice_id = piper.voice_id(language.code)
        if voice_id not in self._source_embeddings:
            audio, sample_rate = piper.calibration_audio(language.code)
            self._source_embeddings[voice_id] = self._get_converter().embedding(audio, sample_rate)
        return self._source_embeddings[voice_id]

    def preload(self, language: Language) -> None:
        """Carga ya todo lo que va a usar `language` (y descarga lo que falte).
        Si se cargara recién con la primera frase, esa frase tardaría mucho
        más en salir sin que se note por qué, y los errores aparecerían tarde."""
        if self.engine == "openvoice":
            self._get_converter()
            self._source_embedding(language)
        elif language.xtts_code is not None:
            self._get_xtts()
        else:
            self._get_piper().preload(language.code)

    def synthesize(self, text: str, language: Language) -> tuple[np.ndarray, int]:
        if not text.strip():
            return np.array([], dtype=np.float32), 24000

        if self.engine == "openvoice":
            audio, sample_rate = self._get_piper().synthesize(text, language.code)
            converted = self._get_converter().convert(
                audio, sample_rate, self._source_embedding(language), self._target_embedding
            )
            return converted, openvoice.SAMPLE_RATE

        if language.xtts_code is not None:
            tts = self._get_xtts()
            wav = tts.tts(text=text, speaker_wav=self.reference_wav, language=language.xtts_code)
            sample_rate = tts.synthesizer.output_sample_rate
            return np.asarray(wav, dtype=np.float32), sample_rate

        if language.code not in self._warned_languages:
            print(
                f"[clonavoz] Aviso: XTTS-v2 no clona tu voz en '{language.name}'. Se usará una voz "
                "neutra de alta calidad en su lugar (con --voice-engine openvoice sí se clona)."
            )
            self._warned_languages.add(language.code)

        return self._get_piper().synthesize(text, language.code)
