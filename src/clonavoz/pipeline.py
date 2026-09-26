"""Pipeline de streaming en vivo:

  captura de audio -> VAD -> ASR -> traducción -> TTS clonado -> reproducción
  en el micrófono virtual.

Con Parakeet, la traducción es simultánea: el VAD pregunta a `ClauseSplitter`
dónde termina cada idea mientras hablás, y esa parte sale traducida sin
esperar a que termines (ver `simultaneous.py`).

Cada etapa corre en su propio hilo y se comunican por colas, para que una
frase pueda estar transcribiéndose mientras la anterior todavía se está
sintetizando: así se aprovecha el tiempo del CPU/GPU y se reduce la
latencia percibida frente a procesar todo de forma estrictamente secuencial.

El micrófono y la salida se abren en `start()`, desde el hilo principal: si
alguno falla, el error llega a quien llamó (con un mensaje claro) en lugar de
matar un hilo en segundo plano mientras la consola sigue diciendo
"Escuchando...". Ver `audio_io` para cómo se adapta cada dispositivo a su
frecuencia de muestreo nativa.
"""
from __future__ import annotations

import dataclasses
import gc
import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import soundfile as sf

from .asr import SpeechRecognizer
from .audio_devices import SAMPLE_RATE
from .audio_io import AudioDeviceError, AudioOutput, MicrophoneStream, resample
from .config import PerformanceProfile
from .languages import Language, get_language
from .simultaneous import ClauseSplitter
from .timestretch import SpeedUp, shorten_pauses, speed_up
from .translate import Translator
from .vad import StreamingVAD
from .voice_clone import VoiceSynthesizer

_SENTINEL = object()

# Segundos de CPU (de toda la PC) que puede costar cada segundo que hablás:
# entenderlo y decirlo traducido con tu voz. Con más, la traducción se atrasa
# cada vez más mientras hablás, y con la voz clonada se pasa a la rápida.
_MAX_LOAD = 1.5
# Reconocer mientras hablás (para traducir cada idea sin esperar la pausa)
# cuesta ~4 veces más que reconocer una vez por frase: solo si queda CPU.
_PARTIALS_COST = 4.3
_MAX_LOAD_WITH_PARTIALS = 1.4
# Parakeet entiende mucho mejor que Whisper, pero es un modelo grande: si en
# esta PC tarda más que esto por segundo de voz, se usa Whisper (en una PC
# lenta, 3 veces más rápido con frases cortas, ver asr._transcribe_short).
_PARAKEET_MAX_SECONDS = 0.5


@dataclass
class SpeedCheck:
    """Lo que tarda esta PC, medido al arrancar (segundos por cada segundo de voz)."""

    asr: float | None  # en entender lo que decís (con 3 s de tu muestra de voz)
    voice: float | None  # en generar la traducción con la voz elegida
    rhythm: float | None = None  # voz natural: una vez que empezó a sonar

    @property
    def load(self) -> float | None:
        if self.voice is None:
            return None
        return (self.asr if self.asr is not None else 0.3) + self.voice

    @property
    def partials_fit(self) -> bool:
        if self.asr is None or self.voice is None:
            return True
        return _PARTIALS_COST * self.asr + self.voice <= _MAX_LOAD_WITH_PARTIALS


@dataclass
class PipelineStats:
    utterances_processed: int = 0
    last_latency_seconds: float = 0.0
    last_transcript: tuple[str, str] = field(default=("", ""))


@dataclass
class LiveStatus:
    level_db: float  # nivel del micrófono desde la consulta anterior
    speaking: bool  # el VAD está escuchando una frase en curso
    pending: int  # frases esperando/en traducción y síntesis
    playing: bool  # se está reproduciendo una traducción


class LiveVoicePipeline:
    def __init__(
        self,
        profile: PerformanceProfile,
        source_lang: str,
        target_lang: str,
        reference_wav: Path,
        input_device: int | None,
        output_device: int | None,
        on_transcript=None,
        on_message=print,
        allow_engine_fallback: bool = False,
    ) -> None:
        """`allow_engine_fallback`: si esta PC no llega a generar la voz elegida
        en vivo, usar la voz rápida (cuando elegiste la voz "auto")."""
        self.profile = profile
        self._reference_wav = Path(reference_wav)
        self._allow_engine_fallback = allow_engine_fallback
        self.speed: SpeedCheck | None = None
        self.fallback_from: str | None = None  # motor que no llegaba (si se cambió a la voz rápida)
        self.slow_parakeet: float | None = None  # lo que tardaba Parakeet, si se cambió a Whisper
        self.source_language: Language = get_language(source_lang)
        self.target_language: Language = get_language(target_lang)
        self.on_transcript = on_transcript
        self.on_message = on_message
        self.stats = PipelineStats()
        self.error: BaseException | None = None

        self._asr = SpeechRecognizer(profile, self.source_language.code)
        self.asr_name = self._asr.name
        # Con Parakeet se traduce en simultáneo: se corta al final de cada idea
        # mientras seguís hablando (ver simultaneous.py).
        self.simultaneous = self._asr.can_split
        self.translate_while_speaking = self.simultaneous  # se apaga si la PC no da (ver _warm_up)
        splitter = ClauseSplitter(self._asr.transcribe_timed) if self.simultaneous else None
        self._vad = StreamingVAD(max_utterance_seconds=profile.max_utterance_seconds, splitter=splitter)
        self._translator = Translator(
            self.source_language.nllb_code,
            self.target_language.nllb_code,
            device=profile.device,
            pair=(self.source_language.code, self.target_language.code),
        )
        self.translator_name = self._translator.name
        self._synth = VoiceSynthesizer(profile, reference_wav, engine=profile.voice_engine)
        self._synth.preload(self.target_language)

        self._frame_queue: "queue.Queue" = queue.Queue()
        self._text_queue: "queue.Queue" = queue.Queue()
        self._audio_out_queue: "queue.Queue" = queue.Queue()
        self.mic = MicrophoneStream(input_device, on_frame=self._frame_queue.put, frame_size=self._vad.frame_size())
        # La voz natural sale a 24 kHz: si el dispositivo lo acepta, se abre así.
        streaming = self._synth.can_stream(self.target_language)
        self.output = AudioOutput(output_device, preferred_rate=24000 if streaming else None)
        # Se decide en `_warm_up` según lo rápido que genera la voz esta PC.
        self.stream_playback = False
        self._processing = False
        self._playing = False
        # Segundos de traducción generados que todavía no sonaron: si la
        # traducción se atrasa, se habla un poco más rápido para alcanzarte.
        self._backlog = 0.0
        self._backlog_lock = threading.Lock()
        self._last_error = ""
        self._threads: list[threading.Thread] = []
        self._warm_up()

    def _warm_up(self) -> None:
        """Una pasada de prueba por cada modelo al arrancar: la primera llamada
        a cada uno es bastante más lenta (reserva memoria, prepara cálculos), y
        así eso no le toca a tu primera frase. Si la síntesis falla (por
        ejemplo, falta FFmpeg para XTTS), el error se ve ya al arrancar.

        De paso se mide lo que tarda esta PC, y con eso se decide cómo trabajar
        (ver `_adapt_to_speed`)."""
        try:
            self._asr.transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32), self.source_language.whisper_code)
            asr_speed = self._measure_asr()
            text = self._translator.translate("Hola, ¿cómo estás? Te quería contar algo.")
            self._synth.synthesize(text, self.target_language)
            self.speed = SpeedCheck(asr_speed, *self._measure_voice(text))
            self._adapt_to_speed(text)
        except Exception as exc:  # noqa: BLE001 - se informa igual que el error de una frase
            self._report_phrase_error(exc)

    def _measure_asr(self) -> float | None:
        """Segundos que tarda en entender cada segundo que hablás, con 3 s de tu
        muestra de voz (voz de verdad: con silencio tarda distinto)."""
        try:
            audio, rate = sf.read(str(self._reference_wav), dtype="float32")
        except Exception:  # noqa: BLE001 - sin la medición se asume una PC normal
            return None
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        clip = resample(audio, rate, SAMPLE_RATE)[SAMPLE_RATE : 4 * SAMPLE_RATE]
        if len(clip) < SAMPLE_RATE:
            return None
        started = time.perf_counter()
        if self.simultaneous:
            self._asr.transcribe_timed(clip)
        else:
            self._asr.transcribe(clip, self.source_language.whisper_code)
        return (time.perf_counter() - started) / (len(clip) / SAMPLE_RATE)

    def _measure_voice(self, text: str) -> tuple[float | None, float | None]:
        """(segundos por segundo de voz generada, y con la voz natural, el ritmo
        después del primer pedazo, que es lo que importa para ir reproduciendo
        mientras se genera: el primero siempre tarda un poco más)."""
        started = time.perf_counter()
        if not self._synth.can_stream(self.target_language):
            audio, rate = self._synth.synthesize(text, self.target_language)
            seconds = len(audio) / rate
            return ((time.perf_counter() - started) / seconds if seconds else None), None
        chunks, rate = self._synth.stream(text, self.target_language)
        first, total, after_first = None, 0, 0
        for chunk in chunks:
            if first is None:
                first = time.perf_counter()
            else:
                after_first += len(chunk)
            total += len(chunk)
        if not total:
            return None, None
        now = time.perf_counter()
        voice = (now - started) / (total / rate)
        return voice, ((now - first) / (after_first / rate) if after_first else voice)

    def _adapt_to_speed(self, text: str) -> None:
        """Con lo que tarda esta PC: si no llega a generar tu voz clonada en vivo,
        la voz rápida (si elegiste la voz "auto"); reproducir mientras se genera
        solo si da el tiempo; y traducir mientras hablás solo si sobra CPU."""
        speed = self.speed
        if self._asr.name == "Parakeet" and speed.asr is not None and speed.asr > _PARAKEET_MAX_SECONDS:
            self.slow_parakeet = speed.asr
            self._use_whisper()
            self.speed = speed = SpeedCheck(self._measure_asr(), speed.voice, speed.rhythm)
        if (
            self._allow_engine_fallback
            and self._synth.engine != "rapida"
            and speed.load is not None
            and speed.load > _MAX_LOAD
        ):
            fast = self._fast_voice(text)
            if fast is not None:
                self.fallback_from = self._synth.engine
                self._slow_voice = speed.voice
                self._synth = fast
                gc.collect()  # libera la memoria de la otra voz
                self.speed = speed = SpeedCheck(speed.asr, *self._measure_voice(text))
        if speed.rhythm is not None:
            # Ir reproduciendo mientras se genera solo si esta PC genera la voz
            # bastante más rápido de lo que dura: si no, la voz se cortaría.
            self.stream_playback = speed.rhythm < 0.75
            # Mientras hablás, el reconocimiento le quita CPU a la voz: cuanto más
            # justa viene la PC, más colchón antes de empezar a sonar cada frase.
            self.output.STREAM_CUSHION_SECONDS = 0.15 if speed.rhythm < 0.4 else 0.3 if speed.rhythm < 0.6 else 0.45
        if self.simultaneous and not speed.partials_fit:
            self._vad.split_while_speaking = False
            self.translate_while_speaking = False
        for message in self._speed_messages():
            self.on_message(message)

    def _fast_voice(self, text: str) -> VoiceSynthesizer | None:
        """La voz rápida, lista para usar; None si no hay voz de Piper en ese idioma."""
        try:
            fast = VoiceSynthesizer(self.profile, self._reference_wav, engine="rapida")
            fast.preload(self.target_language)
            fast.synthesize(text, self.target_language)
        except Exception:  # noqa: BLE001 - se sigue con la voz que había
            return None
        return fast

    def _use_whisper(self) -> None:
        """Whisper en vez de Parakeet (sin traducir mientras hablás: Whisper no da
        el tiempo de cada palabra), liberando la memoria de Parakeet."""
        # "tiny": el más rápido (y el que siempre se descarga).
        self._asr = SpeechRecognizer(dataclasses.replace(self.profile, whisper_model="tiny"))
        self.asr_name = self._asr.name
        self.simultaneous = self.translate_while_speaking = False
        self._vad = StreamingVAD(max_utterance_seconds=self.profile.max_utterance_seconds)
        gc.collect()
        self._asr.transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32), self.source_language.whisper_code)

    def _speed_messages(self) -> list[str]:
        speed = self.speed
        if speed is None or speed.load is None:
            return []
        understand = f" y {speed.asr:.1f} s en entender cada segundo que hablás" if speed.asr is not None else ""
        messages = []
        if self.slow_parakeet is not None:
            messages.append(
                f"[clonavoz] El reconocimiento más preciso (Parakeet) tarda {self.slow_parakeet:.1f} s por cada "
                f"segundo en esta PC: se usa {self.asr_name}, que acá es más rápido pero se equivoca más."
            )
        return messages + self._voice_messages(speed, understand)

    def _voice_messages(self, speed: SpeedCheck, understand: str) -> list[str]:
        if self.fallback_from is not None:
            return [
                f"[clonavoz] Esta PC tarda {self._slow_voice:.1f} s en generar cada segundo de tu voz clonada"
                f"{understand}: en vivo, la traducción se atrasaría cada vez más.",
                "[clonavoz] Para que la conversación no se atrase se usa la voz rápida (un tono parecido al "
                "tuyo, sin clonar). Para usar tu voz clonada igual, aunque tarde más: --voice-engine natural "
                "(en la versión portable, opción 10 del menú).",
            ]
        if speed.load > _MAX_LOAD:
            messages = [
                f"[clonavoz] Esta PC tarda {speed.voice:.1f} s en generar cada segundo de voz{understand}: "
                "la traducción se va a ir atrasando mientras hablás. Hablá en frases cortas, con pausas."
            ]
            if self._synth.engine != "rapida":
                messages.append(
                    "[clonavoz] Con la voz rápida (--voice-engine rapida; opción 10 del menú en la versión "
                    "portable) sale bastante antes, pero no es tu voz clonada."
                )
            return messages
        if speed.load > 1.0:
            return ["[clonavoz] Esta PC va justa: en frases largas la traducción se atrasa un poco y se pone al día en las pausas."]
        return []

    def start(self) -> None:
        """Abre la salida y el micrófono y arranca los hilos. Si un
        dispositivo no se puede abrir, lanza AudioDeviceError."""
        self.output.start()
        try:
            self.mic.start()
        except AudioDeviceError:
            self.output.close()
            raise
        self._threads = [
            threading.Thread(target=self._vad_loop, daemon=True, name="vad"),
            threading.Thread(target=self._synthesis_loop, daemon=True, name="asr-mt-tts"),
            threading.Thread(target=self._playback_loop, daemon=True, name="reproduccion"),
        ]
        for t in self._threads:
            t.start()

    def stop(self) -> None:
        self.mic.close()
        self._frame_queue.put(_SENTINEL)
        self._text_queue.put(_SENTINEL)
        self._audio_out_queue.put(_SENTINEL)
        for t in self._threads:
            t.join(timeout=2)
        # Si el hilo de reproducción sigue escribiendo una frase larga, no se
        # le cierra el stream por debajo (es un hilo daemon: muere al salir).
        if not any(t.is_alive() for t in self._threads if t.name == "reproduccion"):
            self.output.close()

    def poll_status(self) -> LiveStatus:
        return LiveStatus(
            level_db=self.mic.pop_level_db(),
            speaking=self._vad.is_speaking,
            pending=self._text_queue.qsize() + (1 if self._processing else 0),
            playing=self._playing or self._audio_out_queue.qsize() > 0,
        )

    def _vad_loop(self) -> None:
        try:
            while True:
                frame = self._frame_queue.get()
                if frame is _SENTINEL:
                    return
                utterance = self._vad.push(frame)
                if utterance is not None and len(utterance.audio) > 0:
                    self._text_queue.put((utterance, time.time()))
        except Exception as exc:  # noqa: BLE001 - se reporta en la consola vía self.error
            self.error = exc

    def _synthesis_loop(self) -> None:
        while True:
            item = self._text_queue.get()
            if item is _SENTINEL:
                return
            utterance, start_time = item
            self._processing = True
            try:
                text_src = utterance.text
                if text_src is None:
                    text_src = self._asr.transcribe(utterance.audio, self.source_language.whisper_code)
                if not text_src:
                    continue
                text_tgt = self._translator.translate(text_src)
                if self.on_transcript:
                    self.on_transcript(text_src, text_tgt)
                self.stats.last_transcript = (text_src, text_tgt)

                self.stats.utterances_processed += 1
                self._speak(text_tgt)
                self.stats.last_latency_seconds = time.time() - start_time
            except Exception as exc:  # noqa: BLE001 - una frase con error no debe tumbar el pipeline
                self._report_phrase_error(exc)
            finally:
                self._processing = False

    def _queue_audio(self, item: tuple, seconds: float) -> None:
        with self._backlog_lock:
            self._backlog += seconds
        self._audio_out_queue.put(item)

    def _speak(self, text: str) -> None:
        """Genera la traducción con tu voz y la deja lista para sonar. Se genera
        acá, adelantada a lo que está sonando, así entre frase y frase no quedan
        huecos; con la voz natural, de a pedacitos, que empiezan a sonar apenas
        se generan si no hay nada sonando."""
        if not self.stream_playback:
            audio, rate = self._synth.synthesize(text, self.target_language)
            self._queue_audio(("audio", audio, rate), len(audio) / rate)
            return
        chunks, rate = self._synth.stream(text, self.target_language)
        self._audio_out_queue.put(("stream_start", rate))
        try:
            for chunk in chunks:
                self._queue_audio(("chunk", chunk), len(chunk) / rate)
        finally:
            self._audio_out_queue.put(("stream_end",))

    def _catch_up_speed(self, pending: float) -> float:
        """Si la traducción va atrasada (`pending`: segundos que faltan sonar), un
        poco más rápido y sin cambiar el tono, como un intérprete que se apura
        para alcanzarte: más cuanto más atrasada va (1.1 veces con 1 s de
        atraso, hasta 1.3 veces con 3 s o más)."""
        if pending <= 0.5:
            return 1.0
        return min(1.3, 1.0 + 0.1 * pending)

    def _streamed_chunks(self):
        while True:
            item = self._audio_out_queue.get()
            if item is _SENTINEL:
                self._audio_out_queue.put(_SENTINEL)  # para que el bucle de reproducción termine
                return
            if item[0] == "stream_end":
                return
            yield item[1]
            self._played(len(item[1]) / self._stream_rate)

    def _play_phrase_stream(self, rate: int) -> None:
        """Una frase que llega por pedacitos: suena a medida que llega. Si la
        traducción viene atrasada, se dice un poco más rápido mientras tanto,
        sin esperar a que la frase esté entera."""
        self._stream_rate = rate
        chunks = self._streamed_chunks()
        if self._catch_up_speed(self._backlog) > 1.0:
            chunks = self._faster(chunks, rate)
        self.output.play_stream(chunks, rate)

    def _faster(self, chunks, rate: int):
        """Los pedazos, más rápidos mientras la traducción siga atrasada (y los
        silencios, el doble: así se acortan las pausas); apenas te alcanza, el
        resto de la frase sale a la velocidad normal."""
        stretch = SpeedUp(rate)
        for chunk in chunks:
            speed = self._catch_up_speed(self._backlog)
            if speed == 1.0:
                yield stretch.finish()
                yield chunk
                yield from chunks
                return
            silent = len(chunk) > 0 and float(np.max(np.abs(chunk))) < 0.01
            stretch.speed = 2 * speed if silent else speed
            yield stretch.push(chunk)
        yield stretch.finish()

    def _played(self, seconds: float) -> None:
        with self._backlog_lock:
            self._backlog = max(0.0, self._backlog - seconds)

    def _report_phrase_error(self, exc: Exception) -> None:
        text = str(exc)
        if text == self._last_error:
            self.on_message("[clonavoz] Error procesando una frase (el mismo de arriba): no sale audio.")
            return
        self._last_error = text
        self.on_message(f"[clonavoz] Error procesando una frase (no sale audio): {text}")
        lowered = text.lower()
        if "torchcodec" in lowered or "ffmpeg" in lowered:
            self.on_message(
                "[clonavoz] -> Falta FFmpeg o no es compatible: ver 'Instalar FFmpeg' en el README."
            )
        elif "isin_mps_friendly" in text:
            self.on_message('[clonavoz] -> Corregilo con: pip install "transformers>=4.40,<5.0"')

    def _playback_loop(self) -> None:
        try:
            while True:
                item = self._audio_out_queue.get()
                if item is _SENTINEL:
                    return
                self._playing = True
                try:
                    if item[0] == "stream_start":
                        self._play_phrase_stream(item[1])
                    elif item[0] == "audio":
                        audio, rate = item[1], item[2]
                        speed = self._catch_up_speed(self._backlog)
                        self.output.play(speed_up(shorten_pauses(audio, rate), rate, speed), rate)
                        self._played(len(audio) / rate)
                finally:
                    self._playing = False
        except Exception as exc:  # noqa: BLE001 - se reporta en la consola vía self.error
            self.error = exc
