"""Pipeline de streaming en vivo:

  captura de audio -> VAD -> ASR -> traducción -> TTS clonado -> reproducción
  en el micrófono virtual.

Cada etapa corre en su propio hilo y se comunican por colas, para que una
frase pueda estar transcribiéndose mientras la anterior todavía se está
sintetizando: así se aprovecha el tiempo del CPU/GPU y se reduce la
latencia percibida frente a procesar todo de forma estrictamente secuencial.

La reproducción abre el dispositivo de salida una sola vez, a la frecuencia
de muestreo *nativa* de ese dispositivo (no la que use el motor de síntesis,
que puede ser 24kHz con XTTS o variable con Piper). Muchos dispositivos de
audio en Windows (sobre todo por WASAPI, como el cable virtual VB-CABLE) solo
aceptan la frecuencia con la que están configurados en el sistema — abrir el
stream con cualquier otra frecuencia falla con "Invalid sample rate". Por eso
cada frase generada se remuestrea a la frecuencia del dispositivo antes de
reproducirla.
"""
from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import sounddevice as sd
from scipy.signal import resample as scipy_resample

from .asr import SpeechRecognizer
from .audio_devices import SAMPLE_RATE
from .config import PerformanceProfile
from .languages import Language, get_language
from .translate import Translator
from .vad import StreamingVAD
from .voice_clone import VoiceSynthesizer

_SENTINEL = object()


def _resample(audio: np.ndarray, from_rate: int, to_rate: int) -> np.ndarray:
    if from_rate == to_rate or len(audio) == 0:
        return audio
    target_length = max(1, round(len(audio) * to_rate / from_rate))
    return scipy_resample(audio, target_length).astype(np.float32)


@dataclass
class PipelineStats:
    utterances_processed: int = 0
    last_latency_seconds: float = 0.0
    last_transcript: tuple[str, str] = field(default=("", ""))


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
    ) -> None:
        self.profile = profile
        self.source_language: Language = get_language(source_lang)
        self.target_language: Language = get_language(target_lang)
        self.input_device = input_device
        self.output_device = output_device
        self.on_transcript = on_transcript
        self.stats = PipelineStats()

        self._vad = StreamingVAD(max_utterance_seconds=profile.max_utterance_seconds)
        self._asr = SpeechRecognizer(profile)
        self._translator = Translator(
            self.source_language.nllb_code, self.target_language.nllb_code, device=profile.device
        )
        self._synth = VoiceSynthesizer(profile, reference_wav)

        self._frame_queue: "queue.Queue" = queue.Queue()
        self._text_queue: "queue.Queue" = queue.Queue()
        self._audio_out_queue: "queue.Queue" = queue.Queue()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        self._threads = [
            threading.Thread(target=self._capture_loop, daemon=True, name="captura"),
            threading.Thread(target=self._vad_loop, daemon=True, name="vad"),
            threading.Thread(target=self._synthesis_loop, daemon=True, name="asr-mt-tts"),
            threading.Thread(target=self._playback_loop, daemon=True, name="reproduccion"),
        ]
        for t in self._threads:
            t.start()

    def stop(self) -> None:
        self._stop.set()
        self._frame_queue.put(_SENTINEL)
        self._text_queue.put(_SENTINEL)
        self._audio_out_queue.put(_SENTINEL)
        for t in self._threads:
            t.join(timeout=2)

    def _capture_loop(self) -> None:
        frame_size = self._vad.frame_size()

        def callback(indata, frames, time_info, status):
            # El callback de audio debe ser lo más liviano posible: solo
            # copiamos el bloque y lo encolamos, el trabajo pesado (VAD,
            # ASR, traducción, TTS) ocurre en otros hilos.
            self._frame_queue.put(indata[:, 0].copy())

        with sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            blocksize=frame_size,
            device=self.input_device,
            callback=callback,
        ):
            while not self._stop.is_set():
                time.sleep(0.1)

    def _vad_loop(self) -> None:
        frame_size = self._vad.frame_size()
        while True:
            frame = self._frame_queue.get()
            if frame is _SENTINEL:
                return
            if len(frame) != frame_size:
                continue
            utterance = self._vad.push(frame)
            if utterance is not None and len(utterance) > 0:
                self._text_queue.put((utterance, time.time()))

    def _synthesis_loop(self) -> None:
        while True:
            item = self._text_queue.get()
            if item is _SENTINEL:
                return
            utterance, start_time = item
            try:
                text_src = self._asr.transcribe(utterance, self.source_language.whisper_code)
                if not text_src:
                    continue
                text_tgt = self._translator.translate(text_src)
                if self.on_transcript:
                    self.on_transcript(text_src, text_tgt)
                self.stats.last_transcript = (text_src, text_tgt)

                audio_out, sample_rate = self._synth.synthesize(text_tgt, self.target_language)
                self.stats.utterances_processed += 1
                self.stats.last_latency_seconds = time.time() - start_time
                self._audio_out_queue.put((audio_out, sample_rate))
            except Exception as exc:  # noqa: BLE001 - una frase con error no debe tumbar el pipeline
                print(f"[clonavoz] Error procesando una frase: {exc}")

    def _playback_loop(self) -> None:
        if self.output_device is None:
            device_info = sd.query_devices(kind="output")
        else:
            device_info = sd.query_devices(self.output_device)
        target_rate = int(device_info["default_samplerate"])

        out_stream = sd.OutputStream(
            samplerate=target_rate,
            channels=1,
            dtype="float32",
            device=self.output_device,
        )
        out_stream.start()
        try:
            while True:
                item = self._audio_out_queue.get()
                if item is _SENTINEL:
                    return
                audio, sample_rate = item
                if len(audio) == 0:
                    continue
                audio = _resample(audio, sample_rate, target_rate)
                out_stream.write(audio.reshape(-1, 1))
        finally:
            out_stream.stop()
            out_stream.close()
