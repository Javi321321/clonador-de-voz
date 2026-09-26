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


def other_app_code(freq: float, seconds: float) -> str:
    return (
        f"import numpy as np, sounddevice as sd; r = 48000; t = np.arange(int({seconds} * r)) / r; "
        f"sd.play((0.3 * np.sin(2 * np.pi * {freq} * t)).astype('float32'), r); sd.wait()"
    )


def play_from_other_app(freq: float, seconds: float) -> None:
    subprocess.run([sys.executable, "-c", other_app_code(freq, seconds)], check=True)


def read_path_with_microphone() -> bool:
    """El mismo camino de lectura de call_audio, pero del micrófono predeterminado
    (en estas máquinas, CABLE Output: por donde sale lo que suena)."""
    print("\n== Misma lectura, desde el micrófono predeterminado (sin loopback) ==", flush=True)
    import threading

    from clonavoz.audio_io import MicStats
    from clonavoz.call_audio import FrameAssembler, _WasapiLoopback

    frames: list[np.ndarray] = []
    stop = threading.Event()
    info = {}

    def run() -> None:
        wasapi = _WasapiLoopback(False, capture_endpoint=True)
        try:
            wasapi.open()
            info["format"] = f"{wasapi.rate} Hz, {wasapi.channels} canal(es), {wasapi.dtype.name}"
            assembler = FrameAssembler(wasapi.rate, wasapi.channels, wasapi.dtype, 512, frames.append, MicStats())
            wasapi.read(assembler, stop)
        except Exception as exc:  # noqa: BLE001
            info["error"] = exc
        finally:
            wasapi.close()

    thread = threading.Thread(target=run)
    thread.start()
    time.sleep(0.5)
    play_from_other_app(OTHER_APP, 1.5)
    stop.set()
    thread.join()
    audio = np.concatenate(frames) if frames else np.zeros(0, dtype=np.float32)
    level = tone_db(audio, OTHER_APP)
    print(f"  {info.get('format')} {info.get('error', '')}| tono {level:.0f} dB")
    check(level > -40, "la lectura de paquetes de WASAPI funciona (llega el tono por el micrófono)")
    return level > -40


def run(exclude: bool, loopback_here: bool) -> None:
    print(f"\n== Grabar lo que suena, {'menos clonavoz' if exclude else 'todo'} ==", flush=True)
    frames: list[np.ndarray] = []
    stream = CallAudioStream(on_frame=frames.append, exclude_own_audio=exclude)
    started = time.monotonic()
    stream.start()
    print(f"  {stream.description} | sin clonavoz: {stream.excludes_own_audio}", flush=True)
    check(stream.excludes_own_audio == exclude, "se abrió en el modo pedido")
    # Control, por otro camino: lo que sale del cable virtual (la salida
    # predeterminada de estas máquinas) tiene que tener los dos tonos.
    cable = [
        d["index"] for d in sd.query_devices() if d["max_input_channels"] > 0 and "cable output" in d["name"].lower()
    ]
    control: list[np.ndarray] = []
    recorder = None
    if cable:
        recorder = sd.InputStream(device=cable[0], samplerate=RATE, channels=1, dtype="float32",
                                  callback=lambda data, *_: control.append(data[:, 0].copy()))
        recorder.start()
    time.sleep(0.5)
    play_from_other_app(OTHER_APP, 1.5)
    split = len(frames)
    sd.play(tone(CLONAVOZ, 1.5), 48000)
    sd.wait()
    time.sleep(0.5)
    stream.close()
    if recorder is not None:
        recorder.stop()
        recorder.close()
        heard = np.concatenate(control) if control else np.zeros(0, dtype=np.float32)
        print(f"  control (CABLE Output): otro programa {tone_db(heard, OTHER_APP):.0f} dB, "
              f"clonavoz {tone_db(heard, CLONAVOZ):.0f} dB")
    print(f"  Windows entregó: {stream.diagnostics}")
    elapsed = time.monotonic() - started
    audio = np.concatenate(frames) if frames else np.zeros(0, dtype=np.float32)
    seconds = len(audio) / RATE
    peak = 20 * np.log10(max(np.abs(audio).max(initial=0), 1e-9))
    print(f"  grabado: {seconds:.1f} s en {elapsed:.1f} s | pico {peak:.0f} dB")
    check(seconds > 0.8 * (elapsed - 1.0), "entrega audio (o silencio) todo el tiempo, aunque no suene nada")
    other = tone_db(audio[: split * 512], OTHER_APP)
    own = tone_db(audio[split * 512 :], CLONAVOZ)
    print(f"  tono de otro programa: {other:.0f} dB | tono de clonavoz: {own:.0f} dB")
    check(stream.error is None, f"sin errores al grabar ({stream.error})")
    if not loopback_here:
        print("  (esta máquina no entrega audio por loopback: no se puede verificar qué llega)")
        return
    check(other > -40, "llega lo que suena en otro programa (la llamada)")
    if exclude:
        check(own < -70, "no llega lo que reproduce clonavoz")
    else:
        check(own > -40, "llega también lo que reproduce clonavoz (por eso hay que no escuchar mientras suena)")


def reference_libraries() -> bool:
    """Lo mismo con librerías conocidas, para comparar. Devuelve si alguna grabó
    el tono: en las máquinas virtuales de GitHub, con VB-CABLE como única salida,
    ninguna graba nada por loopback (ceros), y ahí no se puede verificar."""
    heard = []
    print("\n== Referencia: soundcard (loopback de la salida predeterminada) ==", flush=True)
    try:
        import soundcard as sc

        speaker = sc.default_speaker()
        mic = sc.get_microphone(id=str(speaker.name), include_loopback=True)
        child = subprocess.Popen([sys.executable, "-c", other_app_code(OTHER_APP, 2.5)])
        audio = mic.record(samplerate=RATE, numframes=int(4.0 * RATE)).mean(axis=1)
        child.wait()
        print(f"  {speaker.name}: pico {20 * np.log10(max(np.abs(audio).max(initial=0), 1e-9)):.0f} dB, "
              f"tono {tone_db(audio, OTHER_APP):.0f} dB")
        heard.append(tone_db(audio, OTHER_APP) > -40)
    except Exception as exc:  # noqa: BLE001 - es solo una referencia
        print(f"  no se pudo: {type(exc).__name__}: {exc}")
    print("\n== Referencia: PyAudioWPatch (dispositivo [Loopback] de PortAudio) ==", flush=True)
    try:
        import pyaudiowpatch as pyaudio

        pa = pyaudio.PyAudio()
        info = pa.get_default_wasapi_loopback()
        rate, channels = int(info["defaultSampleRate"]), int(info["maxInputChannels"])
        stream = pa.open(format=pyaudio.paFloat32, channels=channels, rate=rate, input=True,
                         input_device_index=info["index"], frames_per_buffer=1024)
        chunks = []
        done = [False]

        def reader() -> None:
            while not done[0]:
                chunks.append(np.frombuffer(stream.read(1024, exception_on_overflow=False), dtype=np.float32))

        import threading

        thread = threading.Thread(target=reader)
        thread.start()
        time.sleep(0.3)
        play_from_other_app(OTHER_APP, 1.5)
        done[0] = True
        thread.join()
        stream.close()
        pa.terminate()
        audio = np.concatenate(chunks).reshape(-1, channels).mean(axis=1) if chunks else np.zeros(0)
        from scipy.signal import resample_poly

        audio = resample_poly(audio, RATE, rate)
        print(f"  {info['name']}: pico {20 * np.log10(max(np.abs(audio).max(initial=0), 1e-9)):.0f} dB, "
              f"tono {tone_db(audio, OTHER_APP):.0f} dB")
        heard.append(tone_db(audio, OTHER_APP) > -40)
    except Exception as exc:  # noqa: BLE001 - es solo una referencia
        print(f"  no se pudo: {type(exc).__name__}: {exc}")
    return any(heard)


def main() -> None:
    print(f"Windows {sys.getwindowsversion().build} | salida predeterminada: {sd.query_devices(kind='output')['name']}")
    read_path_with_microphone()
    loopback_here = reference_libraries()
    supported = process_loopback_supported()
    print(f"Grabar todo menos clonavoz: {'se puede' if supported else 'no (Windows viejo)'}")
    for exclude in ([True] if supported else []) + [False]:
        try:
            run(exclude, loopback_here)
        except Exception as exc:  # noqa: BLE001 - que se vea y que siga con el otro modo
            check(False, f"{type(exc).__name__}: {exc}")
    if FAILURES:
        print(f"\nFALLARON {len(FAILURES)} verificaciones")
        sys.exit(1)
    print("\nTODO OK")


if __name__ == "__main__":
    main()
