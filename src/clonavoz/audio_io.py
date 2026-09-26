"""Entrada y salida de audio, robustas ante lo que acepte cada dispositivo.

Micrófono: entrega siempre bloques mono de `frame_size` muestras a 16 kHz,
que es lo que esperan Silero VAD y Whisper. En Windows muchos micrófonos por
WASAPI o WDM-KS solo abren a la frecuencia configurada en el sistema
(44.1/48 kHz) y fallan con "Invalid sample rate" si se les pide 16 kHz: en
ese caso se abre el dispositivo a su frecuencia nativa y se remuestrea acá,
bloque a bloque. También mide el nivel de entrada (para el medidor de la
consola) y junta estadísticas para diagnosticar por qué "el micrófono no se
mueve": si no llega ningún audio, si llega silencio absoluto (típico de los
permisos de micrófono de Windows) o si el nivel es demasiado bajo.

Salida: se abre una sola vez a la frecuencia *nativa* del dispositivo (no la
que use el motor de síntesis, que puede ser 24kHz con XTTS o variable con
Piper), por el mismo motivo: muchos dispositivos, como el cable virtual
VB-CABLE por WASAPI, solo aceptan su frecuencia configurada. Cada frase se
remuestrea a esa frecuencia antes de reproducirla.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass

import numpy as np
import sounddevice as sd
from scipy.signal import butter, sosfilt
from scipy.signal import resample as scipy_resample

from .audio_devices import SAMPLE_RATE, describe_device


class AudioDeviceError(RuntimeError):
    """No se pudo abrir un dispositivo de audio. El mensaje ya está pensado
    para mostrárselo al usuario tal cual."""


def to_dbfs(peak: float) -> float:
    return 20 * math.log10(peak) if peak > 1e-6 else -120.0


def resample(audio: np.ndarray, from_rate: int, to_rate: int) -> np.ndarray:
    """Remuestrea un audio completo (una frase ya sintetizada)."""
    if from_rate == to_rate or len(audio) == 0:
        return audio
    target_length = max(1, round(len(audio) * to_rate / from_rate))
    return scipy_resample(audio, target_length).astype(np.float32)


class StreamResampler:
    """Remuestreo en streaming: el resultado no depende de cómo venga cortado
    el audio en bloques (no hay "clicks" entre un bloque y el siguiente).

    Filtro pasa-bajos con estado (anti-aliasing al bajar de frecuencia) +
    interpolación lineal con la fase arrastrada entre bloques. La calidad
    sobra para VAD y reconocimiento de voz."""

    def __init__(self, from_rate: int, to_rate: int) -> None:
        self._step = from_rate / to_rate
        self._sos = None
        self._zi = None
        if to_rate < from_rate:
            self._sos = butter(8, 0.45 * to_rate, fs=from_rate, output="sos")
            self._zi = np.zeros((self._sos.shape[0], 2))
        # Posición (en muestras de entrada) de la próxima muestra de salida,
        # medida desde la última muestra del bloque anterior (`_last`).
        self._pos = 1.0
        self._last = 0.0

    def process(self, block: np.ndarray) -> np.ndarray:
        x = np.asarray(block, dtype=np.float64)
        n = len(x)
        if n == 0:
            return np.zeros(0, dtype=np.float32)
        if self._sos is not None:
            x, self._zi = sosfilt(self._sos, x, zi=self._zi)
        buf = np.concatenate(([self._last], x))
        count = max(0, math.ceil((n - self._pos) / self._step))
        positions = self._pos + self._step * np.arange(count)
        out = np.interp(positions, np.arange(n + 1), buf)
        self._pos += self._step * count - n
        self._last = buf[-1]
        return out.astype(np.float32)


@dataclass
class MicStats:
    blocks: int = 0  # bloques recibidos del dispositivo
    nonzero_blocks: int = 0  # bloques con algo distinto de silencio absoluto
    max_peak: float = 0.0  # pico máximo desde que se abrió (0..1)
    last_block_time: float = 0.0  # time.monotonic() del último bloque


class MicrophoneStream:
    """`keep_recording=True` (para grabar tu muestra de voz): se abre a la
    frecuencia nativa del micrófono, con toda su calidad, y guarda lo grabado
    tal cual (ver `recording`); los bloques de `on_frame` siguen a 16 kHz."""

    def __init__(self, device: int | None, on_frame=None, frame_size: int = 512, keep_recording: bool = False) -> None:
        self.device = device
        self._on_frame = on_frame
        self._frame_size = frame_size
        self._keep_recording = keep_recording
        self._recording: list[np.ndarray] = []
        self._stream = None
        self._resampler: StreamResampler | None = None
        self._pending = np.zeros(0, dtype=np.float32)
        self._level = 0.0
        self.stats = MicStats()
        self.samplerate: int | None = None
        self.channels: int | None = None
        self.description = ""

    def start(self) -> None:
        try:
            info = sd.query_devices(self.device, "input")
        except (ValueError, sd.PortAudioError) as exc:
            which = f"[{self.device}]" if self.device is not None else "predeterminado"
            raise AudioDeviceError(
                f"No se encontró el micrófono {which}: {exc}\n"
                "Revisá la lista con `clonavoz devices` y elegí uno con --input-device."
            ) from exc

        index = info["index"]
        native_rate = int(info["default_samplerate"])
        # Primero 16 kHz mono (sin remuestrear); si el dispositivo no lo
        # acepta, su frecuencia nativa y, por último, estéreo. Para grabar tu
        # muestra de voz, primero la nativa.
        rates = [native_rate, SAMPLE_RATE] if self._keep_recording else [SAMPLE_RATE, native_rate]
        attempts = [
            (rate, channels)
            for rate in dict.fromkeys(rates)
            for channels in dict.fromkeys([1, min(2, int(info["max_input_channels"]))])
        ]
        errors = []
        for rate, channels in attempts:
            self.samplerate, self.channels = rate, channels
            self._resampler = StreamResampler(rate, SAMPLE_RATE) if rate != SAMPLE_RATE else None
            try:
                stream = sd.InputStream(
                    device=index,
                    samplerate=rate,
                    channels=channels,
                    dtype="float32",
                    blocksize=round(self._frame_size * rate / SAMPLE_RATE),
                    latency="low",  # tu voz llega ~0.1 s antes que con el buffer grande
                    callback=self._callback,
                )
            except sd.PortAudioError as exc:
                errors.append(f"{rate} Hz, {channels} canal(es): {exc}")
                continue
            try:
                stream.start()
            except sd.PortAudioError as exc:
                stream.close()
                errors.append(f"{rate} Hz, {channels} canal(es): {exc}")
                continue
            self._stream = stream
            self.description = describe_device(index)
            if self._keep_recording:
                self.description += f", grabando a {rate} Hz"
            elif rate != SAMPLE_RATE:
                self.description += f", abierto a {rate} Hz y remuestreado a {SAMPLE_RATE} Hz"
            return

        raise AudioDeviceError(
            f"No se pudo abrir el micrófono {describe_device(index)}:\n  "
            + "\n  ".join(errors)
            + "\nProbá con otro micrófono con --input-device (ver `clonavoz devices`; en Windows "
            "los dispositivos 'MME' suelen ser los más compatibles) y revisá que ningún otro "
            "programa lo esté usando en modo exclusivo."
        )

    def _callback(self, indata, frames, time_info, status) -> None:
        # Corre en el hilo de audio: tiene que ser liviano. El trabajo pesado
        # (VAD, ASR, traducción, TTS) ocurre en otros hilos.
        block = indata[:, 0] if self.channels == 1 else indata.mean(axis=1)
        peak = float(np.max(np.abs(block))) if len(block) else 0.0
        stats = self.stats
        stats.blocks += 1
        stats.last_block_time = time.monotonic()
        if peak > 0.0:
            stats.nonzero_blocks += 1
        stats.max_peak = max(stats.max_peak, peak)
        self._level = max(self._level, peak)
        if self._keep_recording:
            self._recording.append(np.array(block, dtype=np.float32))

        if self._on_frame is None:
            return
        if self._resampler is not None:
            block = self._resampler.process(block)
        self._pending = np.concatenate((self._pending, block))
        size = self._frame_size
        while len(self._pending) >= size:
            self._on_frame(self._pending[:size].copy())
            self._pending = self._pending[size:]

    def recording(self) -> tuple[np.ndarray, int]:
        """Todo lo grabado (con `keep_recording`), a la frecuencia a la que se abrió."""
        audio = np.concatenate(self._recording) if self._recording else np.zeros(0, dtype=np.float32)
        return audio, self.samplerate or SAMPLE_RATE

    def pop_level_db(self) -> float:
        """Nivel pico (dBFS) desde la última llamada; para el medidor."""
        level, self._level = self._level, 0.0
        return to_dbfs(level)

    def close(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except sd.PortAudioError:
                pass


class AudioOutput:
    """Salida mono; `play` acepta audio a cualquier frecuencia y bloquea hasta
    que se terminó de encolar. Se abre a `preferred_rate` (la del motor de voz,
    así no hay que remuestrear) si el dispositivo la acepta, y si no a su
    frecuencia nativa."""

    # Antes de empezar a sonar una frase que llega por partes, se junta este
    # colchón: si una parte se demora un poco, no se corta la voz. En una PC
    # que genera la voz muy rápido alcanza con menos (ver pipeline._warm_up).
    STREAM_CUSHION_SECONDS = 0.25
    # Buffer de la placa de sonido. Con el mínimo ("low": ~9 ms en Linux) la voz
    # se entrecorta mientras la PC genera la frase siguiente (medido en una
    # videollamada de prueba: se perdían sílabas); con 0.15 s no se corta y la
    # traducción suena apenas después (en Windows, MME ya usaba 0.09 s).
    OUTPUT_LATENCY_SECONDS = 0.15

    def __init__(self, device: int | None, preferred_rate: int | None = None) -> None:
        self.device = device
        self.preferred_rate = preferred_rate
        self._stream = None
        self.samplerate: int | None = None
        self.channels: int | None = None
        self.description = ""

    def start(self) -> None:
        try:
            info = sd.query_devices(self.device, "output")
        except (ValueError, sd.PortAudioError) as exc:
            which = f"[{self.device}]" if self.device is not None else "predeterminada"
            raise AudioDeviceError(
                f"No se encontró la salida de audio {which}: {exc}\n"
                "Revisá la lista con `clonavoz devices` y elegí una con --output-device."
            ) from exc

        index = info["index"]
        rates = [r for r in dict.fromkeys([self.preferred_rate, int(info["default_samplerate"])]) if r]
        errors = []
        for rate in rates:
            for channels in dict.fromkeys([1, min(2, int(info["max_output_channels"]))]):
                try:
                    stream = sd.OutputStream(
                        device=index, samplerate=rate, channels=channels, dtype="float32",
                        latency=self.OUTPUT_LATENCY_SECONDS,
                    )
                except sd.PortAudioError as exc:
                    errors.append(f"{rate} Hz, {channels} canal(es): {exc}")
                    continue
                try:
                    stream.start()
                except sd.PortAudioError as exc:
                    stream.close()
                    errors.append(f"{rate} Hz, {channels} canal(es): {exc}")
                    continue
                self._stream, self.samplerate, self.channels = stream, rate, channels
                self.description = describe_device(index)
                return

        raise AudioDeviceError(
            f"No se pudo abrir la salida {describe_device(index)}:\n  "
            + "\n  ".join(errors)
            + "\nProbá con otra salida con --output-device (ver `clonavoz devices`; en Windows "
            "los dispositivos 'MME' suelen ser los más compatibles)."
        )

    def play(self, audio: np.ndarray, sample_rate: int) -> None:
        if self._stream is None or len(audio) == 0:
            return
        audio = resample(np.asarray(audio, dtype=np.float32), sample_rate, self.samplerate)
        self._write(audio)

    def play_stream(self, chunks, sample_rate: int) -> None:
        """Reproduce una frase que llega por partes (`chunks`, a `sample_rate`),
        a medida que llegan: empieza a sonar sin esperar la frase entera."""
        if self._stream is None:
            return
        resampler = StreamResampler(sample_rate, self.samplerate) if sample_rate != self.samplerate else None
        cushion = int(self.STREAM_CUSHION_SECONDS * self.samplerate)
        pending: list[np.ndarray] = []
        buffered = 0
        for chunk in chunks:
            chunk = np.asarray(chunk, dtype=np.float32).reshape(-1)
            if resampler is not None:
                chunk = resampler.process(chunk)
            if pending is not None:
                pending.append(chunk)
                buffered += len(chunk)
                if buffered < cushion:
                    continue
                chunk, pending = np.concatenate(pending), None
            self._write(chunk)
        if pending:
            self._write(np.concatenate(pending))

    def _write(self, audio: np.ndarray) -> None:
        if self._stream is None or len(audio) == 0:
            return
        frames = np.repeat(audio.reshape(-1, 1), self.channels, axis=1)
        try:
            self._stream.write(frames)
        except sd.PortAudioError as exc:
            raise AudioDeviceError(f"Se cortó la salida de audio {self.description}: {exc}") from exc

    def close(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except sd.PortAudioError:
                pass


def beep_signal(sample_rate: int, beeps: int = 3) -> np.ndarray:
    """Pitidos de 440 Hz (0.5 s con 0.25 s de silencio entre ellos)."""
    t = np.arange(int(0.5 * sample_rate)) / sample_rate
    beep = 0.3 * np.sin(2 * np.pi * 440 * t)
    fade = min(len(beep) // 2, int(0.01 * sample_rate))
    ramp = np.linspace(0.0, 1.0, fade)
    beep[:fade] *= ramp
    beep[len(beep) - fade :] *= ramp[::-1]
    gap = np.zeros(int(0.25 * sample_rate))
    return np.concatenate([np.concatenate([beep, gap]) for _ in range(beeps)]).astype(np.float32)


def diagnose_microphone(
    stats: MicStats, heard_speech: bool | None = None, windows: bool = False, expect_voice: bool = True
) -> list[str]:
    """Posibles causas (en español) de que "el micrófono no se mueva", a
    partir de lo que llegó del dispositivo. Lista vacía si todo parece bien.

    Con `expect_voice=False` (mientras la traducción está corriendo y quizás
    todavía nadie habló) solo se reportan fallas seguras: que no llegue
    audio o que llegue silencio absoluto."""
    if stats.blocks == 0:
        return [
            "El micrófono no está entregando audio. Puede estar desconectado o en uso por otro "
            "programa en modo exclusivo. Probá con otro --input-device (ver `clonavoz devices`)."
        ]
    if stats.nonzero_blocks == 0:
        problems = [
            "Llega silencio absoluto (todo ceros) del micrófono: no es tu micrófono real (¿es el "
            "micrófono virtual?), está silenciado/desactivado, o el sistema lo está bloqueando. "
            "Revisá cuál se usa con `clonavoz devices` y elegí el tuyo con --input-device."
        ]
        if windows:
            problems.append(
                "En Windows: Configuración > Privacidad y seguridad > Micrófono > activá "
                "'Acceso al micrófono' y 'Permitir que las aplicaciones de escritorio accedan al micrófono'."
            )
        return problems
    if not expect_voice:
        return []
    peak_db = to_dbfs(stats.max_peak)
    if peak_db < -45:
        return [
            f"El nivel es muy bajo (máximo {peak_db:.0f} dB): probablemente es el micrófono equivocado "
            "o su volumen de grabación está casi en cero. Revisá el volumen en la configuración de "
            "sonido del sistema o elegí otro con --input-device (ver `clonavoz devices`)."
        ]
    if heard_speech is False:
        return [
            f"Llega sonido (máximo {peak_db:.0f} dB) pero no se detectó voz. Hablá más cerca del "
            "micrófono, o revisá que sea el micrófono correcto."
        ]
    return []
