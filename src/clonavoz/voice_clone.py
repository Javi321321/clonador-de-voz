"""Síntesis de voz con tu timbre. Cuatro motores:

- "natural" (por defecto cuando está descargado): Pocket TTS genera la frase
  ya con tu voz, clonada de tu muestra; es el que más se parece a vos y el más
  natural, y es rápido en CPU. Habla inglés, español, francés, alemán,
  portugués, italiano y neerlandés; en los demás idiomas se usa "openvoice".
  Ver `pocket_voice.py`.
- "openvoice" (liviano, sin descargas extra): Piper dice la frase en el idioma destino
  y el conversor de OpenVoice V2 le pone tu timbre. Unas 10 veces más rápido
  que XTTS-v2 en CPU, usa poca memoria y clona tu voz en todos los idiomas
  que tienen voz de Piper (~37). Con una voz base de tono parecido al tuyo,
  en nuestras pruebas se parece a la voz original tanto o más que XTTS-v2.
- "rapida" (para notebooks muy lentas): Piper dice la frase con la voz base
  de tono más parecido al tuyo (grave o aguda), sin clonar tu timbre. Se
  parece bastante menos a vos (0.70 contra 0.93 de "natural" en nuestras
  pruebas), pero es ~10 veces más rápida: en una notebook que no llega a
  generar tu voz clonada en vivo, la conversación no se atrasa. El programa
  la elige solo en ese caso (ver pipeline._check_speed).
- "xtts" (por defecto con GPU NVIDIA): XTTS-v2 genera la voz clonada
  directamente, con una entonación algo más natural, pero es pesado: en CPU
  cada frase tarda varios segundos. Clona en ~17 idiomas; para el resto usa
  una voz neutra de Piper. Requiere `pip install "coqui-tts[codec]"`.
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import numpy as np
import soundfile as sf

from . import openvoice, pocket_voice
from .config import PerformanceProfile
from .languages import Language
from .piper_tts import PiperSynthesizer, median_pitch

ENGINES = ("natural", "openvoice", "rapida", "xtts")
ENGINE_NAMES = {
    "natural": "natural (Pocket TTS: tu voz clonada)",
    "openvoice": "liviano (Piper + OpenVoice)",
    "rapida": "rápida (Piper con un tono parecido al tuyo, sin clonar)",
    "xtts": "XTTS-v2",
}


def xtts_installed() -> bool:
    try:
        import TTS  # noqa: F401
    except ImportError:
        return False
    return True


def choose_engine(requested: str, default: str, language: Language) -> tuple[str, str | None]:
    """El motor de voz a usar para hablar en `language` y, si corresponde, un
    aviso para el usuario. `requested` es lo que pidió ("auto" = elegir solo) y
    `default`, el del perfil de rendimiento. Lanza RuntimeError si pidió un
    motor que no está instalado o descargado."""
    if requested == "auto":
        if pocket_voice.cloning_ready(language.code):
            return "natural", None
        if default == "xtts" and not xtts_installed():
            return "openvoice", None
        return default, None
    if requested == "natural":
        if not pocket_voice.supports(language.code):
            return "openvoice", (
                f"La voz natural todavía no habla {language.name}: se usa el motor liviano (Piper + OpenVoice)."
            )
        if not pocket_voice.cloning_ready(language.code):
            raise RuntimeError(
                f"La voz natural para {language.name} no está descargada. Descargala con "
                f"`clonavoz download-models --languages ... {language.code}` y tu token de Hugging Face "
                "(opción 6 del menú en la versión portable), o usá --voice-engine openvoice."
            )
    if requested == "xtts" and not xtts_installed():
        raise RuntimeError('El motor XTTS no está instalado. Instálalo con: pip install "coqui-tts[codec]"')
    return requested, None


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
        self._pocket: pocket_voice.PocketVoice | None = None
        self._pocket_ready: dict[str, bool] = {}
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

    def _uses_pocket(self, language: Language) -> bool:
        if self.engine != "natural":
            return False
        if language.code not in self._pocket_ready:
            ready = pocket_voice.cloning_ready(language.code)
            self._pocket_ready[language.code] = ready
            if not ready:
                print(
                    f"[clonavoz] La voz natural no está disponible en '{language.name}': se usa el motor "
                    "liviano (Piper + OpenVoice) para ese idioma."
                )
        return self._pocket_ready[language.code]

    def _get_pocket(self) -> pocket_voice.PocketVoice:
        if self._pocket is None:
            self._pocket = pocket_voice.PocketVoice(Path(self.reference_wav))
        return self._pocket

    def can_stream(self, language: Language) -> bool:
        """Si la frase se puede ir reproduciendo mientras se genera."""
        return self._uses_pocket(language)

    def stream(self, text: str, language: Language) -> tuple[Iterator[np.ndarray], int]:
        """(pedazos de audio a medida que se generan, frecuencia). Solo si `can_stream`."""
        pocket = self._get_pocket()
        return pocket.stream(text, language.code), pocket.sample_rate

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
        if self._uses_pocket(language):
            self._get_pocket().preload(language.code)
        elif self.engine == "rapida":
            self._get_piper().preload(language.code)
        elif self.engine in ("natural", "openvoice"):
            self._get_converter()
            self._source_embedding(language)
        elif language.xtts_code is not None:
            self._get_xtts()
        else:
            self._get_piper().preload(language.code)

    def synthesize(self, text: str, language: Language) -> tuple[np.ndarray, int]:
        if not text.strip():
            return np.array([], dtype=np.float32), 24000

        if self._uses_pocket(language):
            return self._get_pocket().synthesize(text, language.code)

        if self.engine == "rapida":
            return self._get_piper().synthesize(text, language.code)

        if self.engine in ("natural", "openvoice"):
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
