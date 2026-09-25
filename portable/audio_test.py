"""Prueba con audio de verdad en Windows: dos cables virtuales VB-CABLE hacen
de micrófono y de videollamada. La corre el CI en una máquina de GitHub (que
no tiene placa de sonido) después de `instalar_vbcable_ci.ps1`:

    "tu voz" (frases en español dichas por Piper)
        -> CABLE Input (2- ...)  ==cable 2==>  CABLE Output (2- ...) = tu micrófono
                                                        |
                                                  clonavoz run
                                                        |
    videollamada (esta prueba graba) <- CABLE Output <==cable 1== CABLE Input

Usa los comandos de la carpeta portable (clonavoz.bat), como el usuario:
  1. `test-audio`: con el micrófono predeterminado de Windows (que es un cable
     virtual, sin voz) tiene que avisarlo y diagnosticar el silencio; con "tu
     micrófono" tiene que dar OK, y sus pitidos tienen que llegar al cable de
     la videollamada.
  2. `enroll`: graba tu voz desde el micrófono.
  3. `run`: traducción en vivo al inglés. Se graba lo que escucha la
     videollamada, se transcribe, se compara con la traducción y se mide la
     demora.

Si Windows no permite dos cables independientes, se usa uno solo para las dos
cosas (y la videollamada escucha también la voz original).

Uso: python audio_test.py <carpeta clonavoz-portable> <carpeta para las grabaciones>
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
OUT = Path(sys.argv[2]).resolve()
# Los mismos datos que usa clonavoz.bat: modelos ya descargados, sin internet.
os.environ["CLONAVOZ_HOME"] = str(ROOT / "datos")
os.environ["HF_HOME"] = str(ROOT / "datos" / "modelos")
os.environ["HF_HUB_OFFLINE"] = "1"

import numpy as np  # noqa: E402
import sounddevice as sd  # noqa: E402
import soundfile as sf  # noqa: E402
from scipy.signal import resample_poly  # noqa: E402

from clonavoz import audio_devices  # noqa: E402
from clonavoz.asr import SpeechRecognizer  # noqa: E402  (carga torch antes que ctranslate2)
from clonavoz.config import get_profile  # noqa: E402
from clonavoz.piper_tts import PiperSynthesizer  # noqa: E402

RATE = 16000  # a esta frecuencia se graba el lado de la videollamada
ENROLL_TEXT = (
    "Hola, esta es una muestra de mi voz para el traductor. Hoy hace un día muy lindo, "
    "así que voy a salir a caminar un rato por el parque con mis amigos."
)
PHRASES = ["Mañana voy a comprar pan y leche.", "Mi perro duerme en el jardín."]
FAILURES: list[str] = []


def check(ok: bool, message: str) -> bool:
    print(f"  {'ok ' if ok else 'MAL'}  {message}", flush=True)
    if not ok:
        FAILURES.append(message)
    return ok


def db(value: float) -> float:
    return 20 * np.log10(max(value, 1e-6))


def words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-záéíóúüñ']+", text.lower()) if len(w) >= 3}


def overlap(expected: str, heard: str) -> float:
    """Qué parte de las palabras de `expected` aparecen en `heard` (0 a 1)."""
    wanted = words(expected)
    return len(wanted & words(heard)) / len(wanted) if wanted else 0.0


def to_16k(audio: np.ndarray, rate: int) -> np.ndarray:
    g = np.gcd(rate, RATE)
    return resample_poly(audio, RATE // g, rate // g).astype(np.float32)


class Recorder:
    """Graba un dispositivo de entrada (mono, 16 kHz) mientras está abierto."""

    def __init__(self, device: int) -> None:
        self._blocks: list[np.ndarray] = []
        self._stream = sd.InputStream(
            device=device, samplerate=RATE, channels=1, dtype="float32", callback=self._callback
        )
        self.started = 0.0

    def _callback(self, indata, frames, time_info, status) -> None:
        self._blocks.append(indata[:, 0].copy())

    def __enter__(self) -> "Recorder":
        self._stream.start()
        self.started = time.monotonic()
        return self

    def __exit__(self, *exc) -> None:
        self._stream.stop()
        self._stream.close()

    def audio(self) -> np.ndarray:
        blocks = list(self._blocks)
        return np.concatenate(blocks) if blocks else np.zeros(0, dtype=np.float32)

    def seconds(self, t: float) -> float:
        """Posición en la grabación (segundos) del instante `t` (time.monotonic)."""
        return t - self.started


def play(device: int, audio: np.ndarray, rate: int) -> None:
    """Reproduce y vuelve cuando terminó de sonar."""
    with sd.OutputStream(device=device, samplerate=rate, channels=1, dtype="float32") as stream:
        stream.write(np.ascontiguousarray(audio, dtype=np.float32).reshape(-1, 1))


class Talker:
    """Repite un audio en un dispositivo (en otro hilo) hasta que se lo detiene."""

    def __init__(self, device: int, audio: np.ndarray, rate: int) -> None:
        pause = np.zeros(int(0.5 * rate), dtype=np.float32)
        self._audio = np.concatenate([audio.astype(np.float32), pause]).reshape(-1, 1)
        self._device, self._rate = device, rate
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        block = self._rate // 10
        with sd.OutputStream(device=self._device, samplerate=self._rate, channels=1, dtype="float32") as stream:
            while not self._stop.is_set():
                for start in range(0, len(self._audio), block):
                    if self._stop.is_set():
                        break
                    stream.write(self._audio[start : start + block])

    def __enter__(self) -> "Talker":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        self._thread.join(timeout=10)


class Clonavoz:
    """`clonavoz.bat <args>` de la carpeta portable, mostrando su salida en vivo."""

    def __init__(self, *args) -> None:
        self.lines: list[tuple[float, str]] = []
        print("  $ clonavoz " + " ".join(map(str, args)), flush=True)
        self._proc = subprocess.Popen(
            ["cmd", "/c", str(ROOT / "clonavoz.bat"), *map(str, args)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=dict(os.environ, PYTHONUNBUFFERED="1"),
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _read(self) -> None:
        for line in self._proc.stdout:
            line = line.rstrip()
            self.lines.append((time.monotonic(), line))
            print(f"    | {line}", flush=True)

    @property
    def output(self) -> str:
        return "\n".join(line for _, line in self.lines)

    def wait(self, timeout: float) -> int:
        try:
            code = self._proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.kill()
            raise AssertionError(f"clonavoz no terminó en {timeout:.0f} s") from None
        self._reader.join(timeout=5)
        return code

    def wait_for(self, pattern: str, timeout: float, after: float = 0.0) -> tuple[float, str]:
        """Espera una línea de salida (regex) que haya llegado después de `after`."""
        end = time.monotonic() + timeout
        while True:
            finished = self._proc.poll() is not None and not self._reader.is_alive()
            for t, line in list(self.lines):
                if t >= after and re.search(pattern, line):
                    return t, line
            if finished:
                raise AssertionError(f"clonavoz terminó (código {self._proc.returncode}) sin mostrar {pattern!r}")
            if time.monotonic() > end:
                raise AssertionError(f"clonavoz no mostró {pattern!r} en {timeout:.0f} s")
            time.sleep(0.1)

    def finished(self) -> bool:
        return self._proc.poll() is not None

    def kill(self) -> None:
        if self._proc.poll() is None:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(self._proc.pid)], capture_output=True)
            self._proc.wait(timeout=30)
        self._reader.join(timeout=5)


def sound_span(audio: np.ndarray, since: float, threshold_db: float = -40.0) -> tuple[float, float, bool] | None:
    """Primer tramo con sonido desde `since` (segundos): (comienzo, fin, terminó).
    Terminó = ya hubo 1.5 s de silencio después."""
    hop = RATE // 50  # tramos de 20 ms
    n = len(audio) // hop
    if n == 0:
        return None
    loud = np.abs(audio[: n * hop]).reshape(n, hop).max(axis=1) > 10 ** (threshold_db / 20)
    first = int(since * 50)
    found = np.flatnonzero(loud[first:])
    if len(found) == 0:
        return None
    onset = last = first + int(found[0])
    for i in range(onset, n):
        if loud[i]:
            last = i
        elif i - last >= 75:
            return onset / 50, (last + 1) / 50, True
    return onset / 50, (last + 1) / 50, False


def find_cables() -> list[tuple[audio_devices.AudioDevice, audio_devices.AudioDevice]]:
    """(punta donde se reproduce, punta que se graba) de cada cable, por MME
    (lo que elige clonavoz por defecto)."""
    mme = next(i for i, api in enumerate(sd.query_hostapis()) if api["name"] == "MME")
    pairs = []
    for dev in audio_devices.list_devices():
        if dev.hostapi == mme and dev.max_output_channels > 0 and dev.name.lower().startswith("cable input"):
            recording_end = audio_devices.find_virtual_mic_input(dev)
            if recording_end is not None:
                pairs.append((dev, recording_end))
    return pairs


def tone_level(audio: np.ndarray, freq: float) -> float:
    if len(audio) < RATE // 2:
        return 0.0
    audio = audio[len(audio) // 4 : -len(audio) // 4]  # sin los bordes
    spectrum = np.abs(np.fft.rfft(audio * np.hanning(len(audio)))) / len(audio)
    freqs = np.fft.rfftfreq(len(audio), 1 / RATE)
    return float(spectrum[np.abs(freqs - freq) < 15].max())


def check_cables(pairs) -> list[tuple[bool, bool]]:
    """Pone un tono distinto en cada cable a la vez y se fija qué llega a cada
    punta. Devuelve, por cable: (le llega su tono, no le llega el del otro)."""
    freqs = [500.0, 1200.0][: len(pairs)]
    recorders = [Recorder(rec.index) for _, rec in pairs]
    for recorder in recorders:
        recorder.__enter__()
    try:
        time.sleep(0.3)
        players = [
            threading.Thread(target=play, args=(out.index, 0.3 * np.sin(2 * np.pi * f * np.arange(RATE * 2) / RATE), RATE))
            for (out, _), f in zip(pairs, freqs, strict=True)
        ]
        for p in players:
            p.start()
        for p in players:
            p.join()
        time.sleep(0.3)
    finally:
        for recorder in recorders:
            recorder.__exit__()
    result = []
    for (out, rec), recorder, freq in zip(pairs, recorders, freqs, strict=True):
        audio = recorder.audio()
        own = tone_level(audio, freq)
        other = max((tone_level(audio, f) for f in freqs if f != freq), default=0.0)
        peak = float(np.abs(audio).max()) if len(audio) else 0.0
        print(
            f"  {out.name} -> {rec.name}: pico {db(peak):.0f} dB, su tono {db(own):.0f} dB, "
            f"el del otro cable {db(other):.0f} dB"
        )
        if len(audio) and not np.any(audio):
            print("    (llega silencio absoluto: ¿Windows bloquea el acceso al micrófono?)")
        result.append((own > 0.01, other < own / 10))
    return result


def step_wrong_microphone() -> None:
    print("\n== 1a) test-audio con el micrófono predeterminado (el cable, donde nadie habla) ==", flush=True)
    proc = Clonavoz("test-audio", "--skip-output", "--seconds", "4")
    code = proc.wait(timeout=180)
    check("micrófono virtual" in proc.output, "avisa que el micrófono predeterminado es el cable virtual")
    check(code == 1 and "PROBLEMA" in proc.output, "diagnostica que no llega voz (y termina con error)")


def step_test_audio(mic_in, mic_rec, call_rec, voice, voice_rate) -> None:
    print("\n== 1b) test-audio con \"tu micrófono\" mientras hablás ==", flush=True)
    with Talker(mic_in.index, voice, voice_rate):
        proc = Clonavoz("test-audio", "--input-device", mic_rec.index, "--seconds", "6")
        code = proc.wait(timeout=180)
    check(code == 0, "test-audio termina sin problemas")
    check("OK: clonavoz escucha bien tu micrófono" in proc.output, "el medidor escucha tu voz")
    check(
        re.search(rf"OK: los pitidos llegaron a \[{call_rec.index}\]", proc.output) is not None,
        f"los pitidos llegan a la videollamada ([{call_rec.index}] {call_rec.name})",
    )


def step_enroll(mic_in, mic_rec, voice, voice_rate, recognizer) -> None:
    print("\n== 2) enroll: grabar tu voz desde el micrófono ==", flush=True)
    sample = ROOT / "datos" / "mi_voz.wav"
    with Talker(mic_in.index, voice, voice_rate):
        proc = Clonavoz("enroll", "--input-device", mic_rec.index, "--seconds", "12")
        code = proc.wait(timeout=180)
    if not check(code == 0 and sample.exists(), "enroll guardó la muestra de voz"):
        return
    audio, rate = sf.read(str(sample), dtype="float32")
    seconds, peak = len(audio) / rate, float(np.abs(audio).max())
    check(seconds >= 11 and db(peak) > -30, f"la muestra dura {seconds:.1f} s y su pico es {db(peak):.0f} dB")
    heard = recognizer.transcribe(to_16k(audio, rate), "es")
    match = overlap(ENROLL_TEXT, heard)
    check(match >= 0.5, f"en la muestra se entiende lo que se dijo ({match:.0%}): {heard!r}")
    shutil.copy2(sample, OUT / "2_muestra_grabada_con_enroll.wav")


def step_run(mic_in, mic_rec, call_in, call_rec, same_cable: bool, recognizer) -> None:
    print("\n== 3) run: traducción en vivo de español a inglés ==", flush=True)
    base = PiperSynthesizer(speaker_pitch_hz=110)
    phrases = PHRASES[:1] if same_cable else PHRASES
    results = []
    with Recorder(call_rec.index) as call:
        proc = Clonavoz("run", "--source-lang", "es", "--target-lang", "en", "--input-device", mic_rec.index)
        try:
            proc.wait_for("Escuchando", timeout=900)
            check(
                re.search(rf"Salida de la traducción: \[{call_in.index}\]", proc.output) is not None,
                f"la traducción sale por [{call_in.index}] {call_in.name} (elegido solo)",
            )
            time.sleep(2)
            for phrase in phrases:
                if proc.finished():
                    check(False, "clonavoz run se cerró solo")
                    break
                audio, rate = base.synthesize(phrase, "es")
                start = time.monotonic()
                play(mic_in.index, audio, rate)
                end = time.monotonic()
                try:
                    proc.wait_for(r"^\s*en > ", timeout=120, after=start)
                except AssertionError as exc:
                    check(False, str(exc))
                deadline = time.monotonic() + 60
                span = None
                while time.monotonic() < deadline:
                    span = sound_span(call.audio(), call.seconds(end) + (0.3 if same_cable else 0.0))
                    if span and span[2]:
                        break
                    time.sleep(0.2)
                if not same_cable:
                    time.sleep(1.5)  # por si la frase salió en dos partes
                results.append((phrase, audio, rate, start, end, span, time.monotonic()))
        finally:
            proc.kill()
    recording = call.audio()
    sf.write(str(OUT / "3_lo_que_escucha_la_videollamada.wav"), recording, RATE)
    sf.write(
        str(OUT / "1_tu_voz_frases.wav"),
        np.concatenate([np.concatenate([to_16k(a, r), np.zeros(RATE, np.float32)]) for _, a, r, *_ in results]),
        RATE,
    )

    for i, (phrase, _audio, _rate, start, end, span, until) in enumerate(results, 1):
        print(f"\n  Frase {i}: {phrase!r}")
        said = [line.split(">", 1)[1].strip() for t, line in proc.lines if start <= t <= until and re.match(r"^\s*es > ", line)]
        translated = [line.split(">", 1)[1].strip() for t, line in proc.lines if start <= t <= until and re.match(r"^\s*en > ", line)]
        if same_cable:  # después se escucharía a sí mismo: solo cuenta la primera
            said, translated = said[:1], translated[:1]
        said_text, translated_text = " ".join(said), " ".join(translated)
        check(overlap(phrase, said_text) >= 0.5, f"clonavoz entendió del micrófono: {said_text!r}")
        if not check(span is not None, "la traducción llegó a la videollamada"):
            continue
        onset, finish, _ = span
        delay = onset - call.seconds(end)
        piece = recording[max(0, int(onset * RATE) - RATE // 5) : int(finish * RATE) + RATE // 5]
        heard = recognizer.transcribe(piece, "en")
        match = overlap(translated_text, heard)
        print(f"  Traducción de clonavoz: {translated_text!r}")
        print(f"  Lo que se escucha en la videollamada: {heard!r} (pico {db(float(np.abs(piece).max())):.0f} dB)")
        check(match >= 0.5, f"en la videollamada se entiende la traducción ({match:.0%} de las palabras)")
        print(f"  Demora: {delay:.1f} s desde que terminaste la frase hasta que empezó a sonar la traducción")
        if i == 1 and not same_cable:
            before = recording[: max(0, int(onset * RATE) - RATE // 10)]
            check(
                len(before) > 0 and db(float(np.abs(before).max())) < -50,
                "la videollamada no escucha tu voz original, solo la traducción",
            )


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"PortAudio: {sd.get_portaudio_version()[1]}")
    pairs = find_cables()
    for out, rec in pairs:
        print(f"Cable: [{out.index}] {out.name}  ->  [{rec.index}] {rec.name}")
    if not pairs:
        audio_devices.print_devices()
        sys.exit("No se encontró ningún VB-CABLE por MME.")

    print("\n== 0) ¿Los cables pasan el audio, y cada uno por separado? ==", flush=True)
    results = check_cables(pairs)
    working = [pair for pair, (arrives, _) in zip(pairs, results, strict=True) if arrives]
    independent = [pair for pair, (arrives, clean) in zip(pairs, results, strict=True) if arrives and clean]
    auto = audio_devices.find_virtual_output_device()  # la salida que clonavoz elige sola
    auto_index = auto.index if auto is not None else None
    if not working:
        sys.exit("El audio no pasa por ningún cable: no se puede seguir.")
    same_cable = len(independent) < 2
    candidates = working if same_cable else independent
    call = next((p for p in candidates if p[0].index == auto_index), candidates[0])
    mic = call if same_cable else next(p for p in candidates if p is not call)
    if same_cable:
        print("  No hay dos cables independientes: uno solo hace de micrófono y de videollamada a la vez.")
    (mic_in, mic_rec), (call_in, call_rec) = mic, call
    print(f"  Tu micrófono: [{mic_rec.index}] {mic_rec.name} (la prueba habla en [{mic_in.index}] {mic_in.name})")
    print(f"  Videollamada: [{call_rec.index}] {call_rec.name} (clonavoz reproduce en [{call_in.index}] {call_in.name})")

    voice, voice_rate = PiperSynthesizer(speaker_pitch_hz=110).synthesize(ENROLL_TEXT, "es")
    recognizer = SpeechRecognizer(get_profile("medium"))  # Whisper "small", para verificar

    steps = [
        lambda: step_wrong_microphone(),
        lambda: step_test_audio(mic_in, mic_rec, call_rec, voice, voice_rate),
        lambda: step_enroll(mic_in, mic_rec, voice, voice_rate, recognizer),
        lambda: step_run(mic_in, mic_rec, call_in, call_rec, same_cable, recognizer),
    ]
    for step in steps:
        try:
            step()
        except AssertionError as exc:  # un paso trabado no impide probar los demás
            check(False, str(exc))

    print()
    if FAILURES:
        print(f"FALLARON {len(FAILURES)} verificaciones:")
        for failure in FAILURES:
            print(f"  - {failure}")
        sys.exit(1)
    print("TODO OK con audio real")


if __name__ == "__main__":
    main()
