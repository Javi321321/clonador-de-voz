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
    def __init__(self, device: int | None, on_frame=None, frame_size: int = 512) -> None:
        self.device = device
        self._on_frame = on_frame
        self._frame_size = frame_size
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
        # acepta, su frecuencia nativa y, por último, estéreo.
        attempts = [
            (rate, channels)
            for rate in dict.fromkeys([SAMPLE_RATE, native_rate])
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
            if rate != SAMPLE_RATE:
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

        if self._on_frame is None:
            return
        if self._resampler is not None:
            block = self._resampler.process(block)
        self._pending = np.concatenate((self._pending, block))
        size = self._frame_size
        while len(self._pending) >= size:
            self._on_frame(self._pending[:size].copy())
            self._pending = self._pending[size:]

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
    """Salida mono a la frecuencia nativa del dispositivo; `play` acepta audio
    a cualquier frecuencia y bloquea hasta que se terminó de encolar."""

    def __init__(self, device: int | None) -> None:
        self.device = device
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
        rate = int(info["default_samplerate"])
        errors = []
        for channels in dict.fromkeys([1, min(2, int(info["max_output_channels"]))]):
            try:
                stream = sd.OutputStream(device=index, samplerate=rate, channels=channels, dtype="float32")
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
        frames = np.repeat(audio.reshape(-1, 1), self.channels, axis=1)
        self._stream.write(frames)

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
