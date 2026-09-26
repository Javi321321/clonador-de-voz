"""Prueba en Windows real (la corre el CI) de la grabación de lo que suena en
la computadora (call_audio.py): un tono que reproduce otro programa (como
Zoom) tiene que llegar, y uno que reproduce clonavoz no (salvo en el modo sin
exclusión, el de los Windows viejos, donde llega todo).

Uso: python portable/loopback_test.py   (desde la raíz del repositorio)
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import sounddevice as sd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from clonavoz.call_audio import CallAudioStream, process_loopback_supported

RATE = 16000
OTHER_APP, CLONAVOZ = 700, 1100  # Hz de cada tono
FAILURES: list[str] = []


def check(ok: bool, message: str) -> None:
    print(f"  {'ok ' if ok else 'MAL'}  {message}", flush=True)
    if not ok:
        FAILURES.append(message)


def tone_db(audio: np.ndarray, freq: float) -> float:
    if len(audio) < RATE // 2:
        return -120.0
    spectrum = np.abs(np.fft.rfft(audio * np.hanning(len(audio)))) / len(audio)
    freqs = np.fft.rfftfreq(len(audio), 1 / RATE)
    return float(20 * np.log10(max(spectrum[np.abs(freqs - freq) < 10].max(), 1e-9)))


def tone(freq: float, seconds: float, rate: int = 48000) -> np.ndarray:
    t = np.arange(int(seconds * rate)) / rate
    return (0.3 * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def play_from_other_app(freq: float, seconds: float) -> None:
    code = (
        f"import numpy as np, sounddevice as sd; r = 48000; t = np.arange(int({seconds} * r)) / r; "
        f"sd.play((0.3 * np.sin(2 * np.pi * {freq} * t)).astype('float32'), r); sd.wait()"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


def run(exclude: bool) -> None:
    print(f"\n== Grabar lo que suena, {'menos clonavoz' if exclude else 'todo'} ==", flush=True)
    frames: list[np.ndarray] = []
    stream = CallAudioStream(on_frame=frames.append, exclude_own_audio=exclude)
    started = time.monotonic()
    stream.start()
    print(f"  {stream.description} | sin clonavoz: {stream.excludes_own_audio}", flush=True)
    check(stream.excludes_own_audio == exclude, "se abrió en el modo pedido")
    time.sleep(0.5)
    play_from_other_app(OTHER_APP, 1.5)
    split = len(frames)
    sd.play(tone(CLONAVOZ, 1.5), 48000)
    sd.wait()
    time.sleep(0.5)
    stream.close()
    elapsed = time.monotonic() - started
    audio = np.concatenate(frames) if frames else np.zeros(0, dtype=np.float32)
    seconds = len(audio) / RATE
    print(f"  grabado: {seconds:.1f} s en {elapsed:.1f} s | pico {20 * np.log10(max(np.abs(audio).max(initial=0), 1e-9)):.0f} dB")
    check(seconds > 0.8 * (elapsed - 1.0), "entrega audio (o silencio) todo el tiempo, aunque no suene nada")
    other = tone_db(audio[: split * 512], OTHER_APP)
    own = tone_db(audio[split * 512 :], CLONAVOZ)
    print(f"  tono de otro programa: {other:.0f} dB | tono de clonavoz: {own:.0f} dB")
    check(other > -40, "llega lo que suena en otro programa (la llamada)")
    if exclude:
        check(own < -70, "no llega lo que reproduce clonavoz")
    else:
        check(own > -40, "llega también lo que reproduce clonavoz (por eso hay que no escuchar mientras suena)")
    check(stream.error is None, f"sin errores al grabar ({stream.error})")


def main() -> None:
    print(f"Windows {sys.getwindowsversion().build} | salida predeterminada: {sd.query_devices(kind='output')['name']}")
    supported = process_loopback_supported()
    print(f"Grabar todo menos clonavoz: {'se puede' if supported else 'no (Windows viejo)'}")
    for exclude in ([True] if supported else []) + [False]:
        try:
            run(exclude)
        except Exception as exc:  # noqa: BLE001 - que se vea y que siga con el otro modo
            check(False, f"{type(exc).__name__}: {exc}")
    if FAILURES:
        print(f"\nFALLARON {len(FAILURES)} verificaciones")
        sys.exit(1)
    print("\nTODO OK")


if __name__ == "__main__":
    main()
