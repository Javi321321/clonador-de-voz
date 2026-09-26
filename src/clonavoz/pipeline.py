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

import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .asr import SpeechRecognizer
from .audio_devices import SAMPLE_RATE
from .audio_io import AudioDeviceError, AudioOutput, MicrophoneStream
from .config import PerformanceProfile
from .languages import Language, get_language
from .simultaneous import ClauseSplitter
from .timestretch import SpeedUp, shorten_pauses, speed_up
from .translate import Translator
from .vad import StreamingVAD
from .voice_clone import VoiceSynthesizer

_SENTINEL = object()


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
    ) -> None:
        self.profile = profile
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
        ejemplo, falta FFmpeg para XTTS), el error se ve ya al arrancar."""
        try:
            self._asr.transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32), self.source_language.whisper_code)
            text = self._translator.translate("Hola, ¿cómo estás? Te quería contar algo.")
            self._synth.synthesize(text, self.target_language)
            if self._synth.can_stream(self.target_language):
                self._choose_stream_playback(text)
        except Exception as exc:  # noqa: BLE001 - se informa igual que el error de una frase
            self._report_phrase_error(exc)

    def _choose_stream_playback(self, text: str) -> None:
        """Ir reproduciendo la voz mientras se genera ahorra casi todo el tiempo de
        generarla, pero solo sirve si esta PC la genera bastante más rápido de lo
        que dura: si no, se cortaría. Se mide el ritmo después del primer pedazo
        (que siempre tarda un poco más): segundos para generar cada segundo de voz."""
        chunks, rate = self._synth.stream(text, self.target_language)
        started, samples = None, 0
        for chunk in chunks:
            if started is None:
                started = time.perf_counter()
            else:
                samples += len(chunk)
        if started is None or samples == 0:
            return
        rhythm = (time.perf_counter() - started) / (samples / rate)
        self.stream_playback = rhythm < 0.75
        # Mientras hablás, el reconocimiento le quita CPU a la voz: cuanto más
        # justa viene la PC, más colchón antes de empezar a sonar cada frase.
        self.output.STREAM_CUSHION_SECONDS = 0.15 if rhythm < 0.4 else 0.3 if rhythm < 0.6 else 0.45

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
