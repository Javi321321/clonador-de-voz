"""Lo que te dicen, traducido a tu idioma: el audio de la llamada (ver
call_audio.py) -> en qué idioma te hablan y qué dicen (ver language_id.py) ->
traducción -> una voz en tus auriculares, y el texto en pantalla.

Si te hablan en tu idioma, solo se muestra el texto (ya lo escuchaste).

La voz con que escuchás la traducción:

- "parecida" (por defecto): una voz de tu idioma, de hombre o de mujer según
  el tono de cada frase de quien habla (no es su voz: en una llamada de varios,
  cada uno suena distinto).
- "clonada": su propia voz, clonada de lo que va diciendo en la llamada (a
  partir de los primeros ~8 s). Solo con su permiso: clonar la voz de alguien
  sin su consentimiento no está bien, y los creadores de la voz natural lo
  prohíben. Suena solo en tus auriculares (nunca se manda a la llamada) y no
  se guarda en ningún lado.
- "ninguna": solo el texto en pantalla (subtítulos).
"""
from __future__ import annotations

import dataclasses
import queue
import threading
from collections import deque

import numpy as np
import psutil

from . import openvoice, parakeet_asr, pocket_voice
from .asr import SpeechRecognizer
from .audio_devices import SAMPLE_RATE
from .audio_io import AudioOutput, MicrophoneStream
from .call_audio import CallAudioStream
from .config import PerformanceProfile
from .language_id import LanguageTracker
from .languages import Language, get_language
from .pipeline import LiveVoicePipeline, PipelineStats
from .piper_tts import PiperSynthesizer, listen_voices, median_pitch
from .translate import Translator
from .vad import StreamingVAD

THEIR_VOICES = ("parecida", "clonada", "ninguna")
DEFAULT_PRIORITY = ("en", "pt")  # los idiomas en que es más probable que te hablen
_HIGH_PITCH_HZ = 160  # desde acá, la voz de mujer
_CLONE_AFTER_SECONDS = 8.0  # de su voz, para clonarla (si dio permiso)
CONSENT_NOTICE = (
    "Clonar la voz de otra persona requiere su permiso: avisale que su voz se va a clonar para "
    "traducírtela y pedile que acepte antes. Su voz clonada suena solo en tus auriculares, nunca "
    "se manda a la llamada y no se guarda."
)


class CallRecognizer:
    """Entiende lo que te dicen, en el idioma que sea: Whisper "tiny" reconoce
    el idioma por el sonido, y el texto sale de Parakeet (si la PC lo tiene y
    entiende ese idioma: se equivoca bastante menos) o de Whisper, aprovechando
    lo que ya calculó para el idioma."""

    def __init__(self, profile: PerformanceProfile, priority: tuple[str, ...], parakeet: bool = True) -> None:
        self._whisper = SpeechRecognizer(dataclasses.replace(profile, whisper_model="tiny"))
        self._parakeet = None
        if parakeet and parakeet_asr.enough_memory() and parakeet_asr.ready():
            cores = psutil.cpu_count(logical=False) or 2
            self._parakeet = parakeet_asr.shared(threads=max(1, min(4, cores)))
        self.tracker = LanguageTracker(priority=priority)
        self.name = "Parakeet (y Whisper tiny para el idioma)" if self._parakeet else "Whisper tiny"

    def warm_up(self) -> None:
        silence = np.zeros(SAMPLE_RATE, dtype=np.float32)
        self._whisper.language_probabilities(silence)
        if self._parakeet is not None:
            self._parakeet.transcribe(silence)

    def recognize(self, audio: np.ndarray) -> tuple[str, str]:
        """(idioma, texto) de una frase. Texto vacío si no dijo nada."""
        probabilities, encoded = self._whisper.language_probabilities(audio)
        seconds = len(audio) / SAMPLE_RATE
        if self._parakeet is not None:
            text = self._parakeet.transcribe(audio)
            code = self.tracker.update(probabilities, seconds, text)
            if parakeet_asr.supports(code):
                return self._finish(code, text)
        else:
            # Sin Parakeet, el texto depende del idioma que se elija. Si por el
            # sonido parece que cambió de idioma, se transcribe en ese y las
            # palabras lo confirman (o no) antes de decidir.
            heard = self.tracker.heard(probabilities)
            hint = None
            if self.tracker.current is not None and heard != self.tracker.current:
                hint = self._whisper.transcribe_encoded(audio, get_language(heard).whisper_code, encoded)
            code = self.tracker.update(probabilities, seconds, hint)
            if hint is not None and code == heard:
                return self._finish(code, hint)
        text = self._whisper.transcribe_encoded(audio, get_language(code).whisper_code, encoded)
        return self._finish(code, text)

    def _finish(self, code: str, text: str) -> tuple[str, str]:
        text = (text or "").strip()
        if not text:
            self.tracker.revert()  # era ruido: no cuenta para saber en qué idioma te hablan
        return code, text


class _OpenVoiceClone:
    """Su voz con el motor liviano: una voz base de tu idioma con su timbre."""

    def __init__(self, audio: np.ndarray, rate: int, piper: PiperSynthesizer, language: Language) -> None:
        self._converter = openvoice.ToneColorConverter.from_pretrained()
        self._piper, self._code = piper, language.code
        self._source = self._converter.embedding(*piper.calibration_audio(language.code))
        self._target = self._converter.embedding(audio, rate)

    def synthesize(self, text: str, code: str) -> tuple[np.ndarray, int]:
        audio, rate = self._piper.synthesize(text, code)
        return self._converter.convert(audio, rate, self._source, self._target), openvoice.SAMPLE_RATE


class TheirVoice:
    """La voz con que escuchás lo que te dicen (ver arriba). Tiene la misma
    forma que voice_clone.VoiceSynthesizer, para el pipeline."""

    def __init__(self, mode: str, language: Language) -> None:
        if mode not in THEIR_VOICES:
            raise ValueError(f"Voz desconocida: '{mode}'. Opciones: {', '.join(THEIR_VOICES)}")
        self.mode = self.engine = mode
        self._language = language
        low, high = listen_voices(language.code)
        self._piper = {
            False: PiperSynthesizer(voices={language.code: low}),
            True: PiperSynthesizer(voices={language.code: high}),
        }
        self._pitches: deque[float] = deque(maxlen=10)
        self._high = False
        self._heard: list[np.ndarray] = []
        self._heard_seconds = 0.0
        self._clone = None
        self._streams = False  # si su voz clonada se puede ir reproduciendo mientras se genera
        self._cloning = False
        self.messages: list[str] = []  # avisos para mostrar (ej. "ya se clonó su voz")

    def preload(self, language: Language) -> None:
        if self.mode == "ninguna":
            return
        for piper in self._piper.values():
            piper.preload(language.code)

    def listen(self, audio: np.ndarray) -> None:
        """Una frase de quien te habla: su tono (para elegir la voz) y, si está
        permitido clonarla, su voz."""
        pitch = median_pitch(audio, SAMPLE_RATE)
        if pitch is not None:
            self._pitches.append(pitch)
        reference = pitch if pitch is not None else (float(np.median(self._pitches)) if self._pitches else None)
        if reference is not None:
            self._high = reference >= _HIGH_PITCH_HZ
        if self.mode == "clonada" and self._clone is None and not self._cloning:
            self._heard.append(np.asarray(audio, dtype=np.float32))
            self._heard_seconds += len(audio) / SAMPLE_RATE
            if self._heard_seconds >= _CLONE_AFTER_SECONDS:
                self._cloning = True
                threading.Thread(target=self._make_clone, daemon=True, name="clonar-su-voz").start()

    def _make_clone(self) -> None:
        audio = np.concatenate(self._heard)
        self._heard = []
        code = self._language.code
        try:
            if pocket_voice.cloning_ready(code):
                clone = pocket_voice.PocketVoiceFromAudio(audio, SAMPLE_RATE)
                clone.preload(code)
                self._streams = True
            else:
                clone = _OpenVoiceClone(audio, SAMPLE_RATE, self._piper[self._high], self._language)
            self._clone = clone
            self.messages.append("[clonavoz] Listo: desde ahora escuchás la traducción con su voz clonada.")
        except Exception as exc:  # noqa: BLE001 - se sigue con la voz parecida
            self.messages.append(f"[clonavoz] No se pudo clonar su voz ({exc}): se sigue con una voz parecida.")

    def can_stream(self, language: Language) -> bool:
        return self._clone is not None and self._streams

    def stream(self, text: str, language: Language):
        return self._clone.stream(text, language.code), self._clone.sample_rate

    def synthesize(self, text: str, language: Language) -> tuple[np.ndarray, int]:
        if self._clone is not None:
            return self._clone.synthesize(text, language.code)
        return self._piper[self._high].synthesize(text, language.code)


class IncomingPipeline(LiveVoicePipeline):
    """Lo que te dicen en la llamada, traducido a `listen_lang` en tus
    auriculares (`output_device`, por defecto la salida predeterminada).

    `priority`: los idiomas en que es más probable que te hablen. `call_device`:
    un dispositivo de entrada con el audio de la llamada, en vez de lo que suena
    en la computadora (ver call_audio.py). `on_transcript(idioma, texto,
    traducción)`: cada frase (traducción None si te hablaron en tu idioma).
    `on_language(idioma)`: cuando cambia el idioma en que te hablan."""

    def __init__(
        self,
        profile: PerformanceProfile,
        listen_lang: str,
        output_device: int | None = None,
        their_voice: str = "parecida",
        priority: tuple[str, ...] = DEFAULT_PRIORITY,
        call_device: int | None = None,
        on_transcript=None,
        on_message=print,
        on_language=None,
        recognizer: CallRecognizer | None = None,
    ) -> None:
        # No se llama a LiveVoicePipeline.__init__: se arma distinto, pero se usan
        # sus hilos, la reproducción y cómo se pone al día si se atrasa.
        self.profile = profile
        self.target_language: Language = get_language(listen_lang)
        self.source_language = None
        self.on_transcript = on_transcript
        self.on_message = on_message
        self.on_language = on_language
        self.stats = PipelineStats()
        self.error: BaseException | None = None
        self.priority = tuple(dict.fromkeys((*priority, self.target_language.code)))
        self.recognizer = recognizer or CallRecognizer(profile, self.priority)
        self.asr_name = self.recognizer.name
        self._translators: dict[str, Translator] = {}
        self._synth = TheirVoice(their_voice, self.target_language)
        self._synth.preload(self.target_language)
        self._vad = StreamingVAD(max_utterance_seconds=max(5.0, profile.max_utterance_seconds))
        self._frame_queue: queue.Queue = queue.Queue()
        self._text_queue: queue.Queue = queue.Queue()
        self._audio_out_queue: queue.Queue = queue.Queue()
        frame_size = self._vad.frame_size()
        if call_device is None:
            self.mic = CallAudioStream(on_frame=self._frame_queue.put, frame_size=frame_size)
        else:
            self.mic = MicrophoneStream(call_device, on_frame=self._frame_queue.put, frame_size=frame_size)
        self.output = AudioOutput(output_device)
        self.stream_playback = True  # si la voz lo permite (su voz clonada con la voz natural)
        self.simultaneous = self.translate_while_speaking = False
        self._processing = False
        self._playing = False
        self.last_played = 0.0
        self._backlog = 0.0
        self._backlog_lock = threading.Lock()
        self._last_error = ""
        self._threads: list[threading.Thread] = []
        self._targets = {}
        self.muted_while = None
        self.language: str | None = None  # en qué idioma te están hablando
        self._warm_up()

    @property
    def translator_name(self) -> str:
        names = sorted({translator.name for translator in self._translators.values()})
        return ", ".join(names) or "(todavía ninguno)"

    def _warm_up(self) -> None:
        try:
            self.recognizer.warm_up()
            for code in self.priority:
                if code != self.target_language.code:
                    self._translator(code)
            if self._synth.mode != "ninguna":
                self._synth.synthesize("Hola.", self.target_language)
        except Exception as exc:  # noqa: BLE001 - se informa igual que el error de una frase
            self._report_phrase_error(exc)

    def _translator(self, code: str) -> Translator:
        if code not in self._translators:
            source = get_language(code)
            self._translators[code] = Translator(
                source.nllb_code, self.target_language.nllb_code, device=self.profile.device,
                pair=(code, self.target_language.code),
            )
        return self._translators[code]

    def _filter_frame(self, frame: np.ndarray) -> np.ndarray:
        # Si se graba todo lo que suena (Windows viejo, o un dispositivo elegido
        # a mano), mientras suena la traducción no se escucha: si no, se
        # volvería a traducir a sí misma.
        if not getattr(self.mic, "excludes_own_audio", False) and self.playing:
            return np.zeros_like(frame)
        return super()._filter_frame(frame)

    def _process(self, utterance) -> None:
        code, text = self.recognizer.recognize(utterance.audio)
        if not text:
            return
        if code != self.language:
            self.language = code
            if self.on_language is not None:
                self.on_language(code)
        if code == self.target_language.code:
            if self.on_transcript:
                self.on_transcript(code, text, None)  # te hablan en tu idioma: ya lo escuchaste
            return
        translated = self._translator(code).translate(text)
        if self.on_transcript:
            self.on_transcript(code, text, translated)
        self.stats.last_transcript = (text, translated)
        self.stats.utterances_processed += 1
        for message in self._synth.messages:
            self.on_message(message)
        self._synth.messages.clear()
        if self._synth.mode == "ninguna" or not translated:
            return
        self._synth.listen(utterance.audio)
        self._speak(translated, self.target_language)
