"""Prueba con audio de verdad en Windows, con el cable virtual VB-CABLE. La
corre el CI en una máquina de GitHub, que no tiene placa de sonido, después
de `instalar_vbcable_ci.ps1`.

Como no hay micrófono, la prueba "habla" (frases en español dichas por Piper)
en la entrada del cable, y clonavoz la escucha por "CABLE Output" igual que a
un micrófono: es un dispositivo de grabación real de Windows (mismo camino:
servicio de audio de Windows, permisos de micrófono, MME). La traducción sale
por ese mismo cable, y la prueba graba "CABLE Output" como lo haría Zoom.

Se usa un solo cable para las dos cosas porque VB-CABLE no se puede instalar
dos veces. Por eso lo grabado tiene también la frase original, y clonavoz
escucharía su propia traducción: se analiza solo lo que suena después de la
frase, y clonavoz se cierra apenas termina de decir la traducción.

Con los comandos de la carpeta portable (clonavoz.bat), como el usuario:
  1. `test-audio` sin elegir micrófono, sin que nadie hable: el micrófono
     predeterminado de Windows es el cable, así que tiene que avisarlo y
     diagnosticar el silencio; y sus pitidos tienen que llegar al cable.
  2. `test-audio` con el micrófono mientras se habla: tiene que dar OK.
  3. `enroll`: graba tu voz desde el micrófono.
  4. `run`: traducción en vivo de español a inglés, una vez por frase. Se
     transcribe lo que llega a la "videollamada", se compara con la
     traducción y se mide la demora.
  5. `escuchar`: alguien te habla en inglés y en portugués (por el cable,
     como si fuera la llamada: en estas máquinas no se puede grabar lo que
     suena, ver loopback_test.py) y la traducción al español tiene que
     verse en pantalla y sonar.

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

RATE = 16000  # a esta frecuencia se graba lo que escucha la "videollamada"
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

    def texts(self, pattern: str, since: float) -> list[str]:
        """El texto de las líneas como "  es > ..." (según `pattern`) desde `since`."""
        return [line.split(">", 1)[1].strip() for t, line in self.lines if t >= since and re.match(pattern, line)]

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

    def kill(self) -> None:
        if self._proc.poll() is None:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(self._proc.pid)], capture_output=True)
            self._proc.wait(timeout=30)
        self._reader.join(timeout=5)

    def check_clean_console(self) -> None:
        """Que el usuario no vea avisos técnicos de las librerías ni errores de Python."""
        noisy = [line for _, line in self.lines if re.search(r"Warning|Traceback", line)]
        check(not noisy, f"la consola no muestra avisos técnicos {noisy[:2] if noisy else ''}")


def sound_span(
    audio: np.ndarray, since: float, threshold_db: float = -40.0, silence: float = 0.8
) -> tuple[float, float, bool] | None:
    """Primer tramo con sonido desde `since` (segundos): (comienzo, fin, terminó).
    Terminó = después hubo `silence` segundos sin sonido."""
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
        elif i - last >= silence * 50:
            return onset / 50, (last + 1) / 50, True
    return onset / 50, (last + 1) / 50, False


def tone_level(audio: np.ndarray, freq: float) -> float:
    if len(audio) < RATE // 2:
        return 0.0
    audio = audio[len(audio) // 4 : -len(audio) // 4]  # sin los bordes
    spectrum = np.abs(np.fft.rfft(audio * np.hanning(len(audio)))) / len(audio)
    freqs = np.fft.rfftfreq(len(audio), 1 / RATE)
    return float(spectrum[np.abs(freqs - freq) < 15].max())


def step_cable(cable_in, cable_out) -> bool:
    print("\n== 0) ¿El cable pasa el audio? ==", flush=True)
    with Recorder(cable_out.index) as recorder:
        time.sleep(0.3)
        play(cable_in.index, 0.3 * np.sin(2 * np.pi * 500 * np.arange(2 * RATE) / RATE), RATE)
        time.sleep(0.3)
    audio = recorder.audio()
    level = tone_level(audio, 500)
    if len(audio) and not np.any(audio):
        print("  (llega silencio absoluto: ¿Windows bloquea el acceso al micrófono?)")
    return check(level > 0.01, f"un tono puesto en la entrada llega a la salida ({db(level):.0f} dB)")


def step_default_microphone(cable_out) -> None:
    print("\n== 1) test-audio sin elegir micrófono y sin que nadie hable ==", flush=True)
    proc = Clonavoz("test-audio", "--seconds", "4")
    code = proc.wait(timeout=180)
    check("micrófono virtual" in proc.output, "avisa que el micrófono predeterminado de Windows es el cable virtual")
    check("PROBLEMA" in proc.output, "diagnostica que de ese micrófono no llega voz")
    check(
        re.search(rf"OK: los pitidos llegaron a \[{cable_out.index}\]", proc.output) is not None,
        "los pitidos llegan al micrófono virtual (lo que escucha la videollamada)",
    )
    check(code == 1, "termina con error, por el micrófono")
    proc.check_clean_console()


def step_microphone_with_voice(cable_in, cable_out, voice, voice_rate) -> None:
    print("\n== 2) test-audio con el micrófono mientras hablás ==", flush=True)
    with Talker(cable_in.index, voice, voice_rate):
        proc = Clonavoz("test-audio", "--input-device", cable_out.index, "--skip-output", "--seconds", "6")
        code = proc.wait(timeout=180)
    check("OK: clonavoz escucha bien tu micrófono" in proc.output, "el medidor escucha tu voz y detecta que es voz")
    check(code == 0, "test-audio termina sin problemas")
    proc.check_clean_console()


def step_enroll(cable_in, cable_out, voice, voice_rate, recognizer) -> None:
    print("\n== 3) enroll: grabar tu voz desde el micrófono ==", flush=True)
    sample = ROOT / "datos" / "mi_voz.wav"
    with Talker(cable_in.index, voice, voice_rate):
        proc = Clonavoz("enroll", "--input-device", cable_out.index, "--seconds", "12")
        code = proc.wait(timeout=180)
    proc.check_clean_console()
    if not check(code == 0 and sample.exists(), "enroll guardó la muestra de voz"):
        return
    shutil.copy2(sample, OUT / "2_muestra_grabada_con_enroll.wav")
    audio, rate = sf.read(str(sample), dtype="float32")
    seconds, peak = len(audio) / rate, float(np.abs(audio).max())
    check(seconds >= 11 and db(peak) > -30, f"la muestra dura {seconds:.1f} s y su pico es {db(peak):.0f} dB")
    heard = recognizer.transcribe(to_16k(audio, rate), "es")
    match = overlap(ENROLL_TEXT, heard)
    check(match >= 0.5, f"en la muestra se entiende lo que se dijo ({match:.0%}): {heard!r}")


def step_run(number: int, phrase: str, cable_in, cable_out, recognizer) -> float | None:
    """Traduce una frase en vivo. Devuelve la demora (s) o None si no salió."""
    print(f"\n== 4.{number}) run: traducción en vivo de {phrase!r} ==", flush=True)
    audio, rate = PiperSynthesizer(speaker_pitch_hz=110).synthesize(phrase, "es")
    sf.write(str(OUT / f"1_tu_voz_frase{number}.wav"), audio, rate)
    span = None
    start = end = time.monotonic()
    with Recorder(cable_out.index) as call:
        proc = Clonavoz("run", "--source-lang", "es", "--target-lang", "en", "--input-device", cable_out.index)
        try:
            proc.wait_for("Escuchando", timeout=900)
            if number == 1:
                check(
                    re.search(rf"Salida de la traducción: \[{cable_in.index}\]", proc.output) is not None,
                    f"elige solo el cable para la traducción ([{cable_in.index}] {cable_in.name})",
                )
            time.sleep(2)
            start = time.monotonic()
            play(cable_in.index, audio, rate)
            end = time.monotonic()
            proc.wait_for(r"^\s*en > ", timeout=120, after=start)
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                span = sound_span(call.audio(), call.seconds(end) + 0.3)
                if span and span[2]:
                    break
                time.sleep(0.1)
        except AssertionError as exc:
            check(False, str(exc))
        finally:
            proc.kill()  # si no, se escucharía a sí mismo (mismo cable) y volvería a traducir
    proc.check_clean_console()
    recording = call.audio()
    sf.write(str(OUT / f"3_videollamada_frase{number}.wav"), recording, RATE)

    said = proc.texts(r"^\s*es > ", start)[:1]
    translated = proc.texts(r"^\s*en > ", start)[:1]
    check(bool(said) and overlap(phrase, said[0]) >= 0.5, f"entendió del micrófono: {said}")
    if not check(span is not None, "la traducción llegó a la videollamada"):
        return None
    onset, finish, _ = span
    piece = recording[max(0, int(onset * RATE) - RATE // 5) : int(finish * RATE) + RATE // 5]
    sf.write(str(OUT / f"3_videollamada_frase{number}_solo_traduccion.wav"), piece, RATE)
    heard = recognizer.transcribe(piece, "en")
    match = overlap(" ".join(translated), heard)
    print(f"  Traducción de clonavoz: {translated}")
    print(f"  Lo que se escucha en la videollamada: {heard!r} (pico {db(float(np.abs(piece).max())):.0f} dB)")
    check(match >= 0.5, f"en la videollamada se entiende la traducción ({match:.0%} de las palabras)")
    delay = onset - call.seconds(end)
    print(f"  Demora: {delay:.1f} s desde que terminaste la frase hasta que empezó a sonar la traducción")
    return delay


THEIR_PHRASES = [
    ("en", "inglés", "Hi! I wanted to ask you about the meeting tomorrow."),
    ("pt", "portugués", "Oi, tudo bem? Você pode me mandar o contrato até amanhã?"),
]


def step_listen(cable_in, cable_out, recognizer) -> list[float]:
    """escuchar: alguien te habla en inglés y en portugués (por el cable, como si
    fuera la llamada) y la traducción al español tiene que sonar (en el mismo
    cable: acá es "tus auriculares") y verse en pantalla."""
    print("\n== 5) escuchar: te hablan en inglés y en portugués ==", flush=True)
    delays = []
    proc = Clonavoz(
        "escuchar", "--call-device", cable_out.index, "--output-device", cable_in.index, "--their-voice", "parecida"
    )
    try:
        proc.wait_for("Escuchando", timeout=900)
        for code, name, phrase in THEIR_PHRASES:
            audio, rate = PiperSynthesizer(speaker_pitch_hz=200).synthesize(phrase, code)
            sf.write(str(OUT / f"5_te_dicen_{code}.wav"), audio, rate)
            with Recorder(cable_out.index) as call:
                time.sleep(1.0)
                start = time.monotonic()
                play(cable_in.index, audio, rate)
                end = time.monotonic()
                proc.wait_for(rf"Te dicen \({name}\)", timeout=120, after=start)
                span = None
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    span = sound_span(call.audio(), call.seconds(end) + 0.3)
                    if span and span[2]:
                        break
                    time.sleep(0.1)
            recording = call.audio()
            sf.write(str(OUT / f"5_traduccion_de_{code}.wav"), recording, RATE)
            said = proc.texts(rf"^\s*Te dicen \({name}\) > ", start)[:1]
            translated = proc.texts(r"^\s*es > ", start)[:1]
            check(bool(said) and overlap(phrase, said[0]) >= 0.5, f"entendió lo que le dijeron en {name}: {said}")
            check(bool(translated), f"lo tradujo al español: {translated}")
            if not check(span is not None, f"la traducción de lo que dijeron en {name} sonó"):
                continue
            onset, finish, _ = span
            piece = recording[max(0, int(onset * RATE) - RATE // 5) : int(finish * RATE) + RATE // 5]
            heard = recognizer.transcribe(piece, "es")
            match = overlap(" ".join(translated), heard)
            print(f"  Se escucha: {heard!r}")
            check(match >= 0.5, f"se entiende la traducción al español ({match:.0%} de las palabras)")
            delays.append(onset - call.seconds(end))
            print(f"  Demora: {delays[-1]:.1f} s desde que terminó de hablar hasta que empezó a sonar")
    except AssertionError as exc:
        check(False, str(exc))
    finally:
        proc.kill()
    proc.check_clean_console()
    return delays


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"PortAudio: {sd.get_portaudio_version()[1]}")
    cable_in = audio_devices.find_virtual_output_device()  # donde clonavoz reproduce, elegido solo
    cable_out = audio_devices.find_virtual_mic_input(cable_in) if cable_in else None
    if cable_in is None or cable_out is None:
        audio_devices.print_devices()
        sys.exit("No se encontró VB-CABLE.")
    print(f"Entrada del cable: {audio_devices.describe_device(cable_in.index)}")
    print(f"Salida del cable (el \"micrófono\"): {audio_devices.describe_device(cable_out.index)}")
    if not step_cable(cable_in, cable_out):
        sys.exit("El audio no pasa por el cable: no se puede seguir.")

    voice, voice_rate = PiperSynthesizer(speaker_pitch_hz=110).synthesize(ENROLL_TEXT, "es")
    recognizer = SpeechRecognizer(get_profile("medium"), "es")  # Parakeet (o Whisper), para verificar
    steps = [
        lambda: step_default_microphone(cable_out),
        lambda: step_microphone_with_voice(cable_in, cable_out, voice, voice_rate),
        lambda: step_enroll(cable_in, cable_out, voice, voice_rate, recognizer),
    ]
    for step in steps:
        try:
            step()
        except AssertionError as exc:  # un paso trabado no impide probar los demás
            check(False, str(exc))
    delays = [step_run(i, phrase, cable_in, cable_out, recognizer) for i, phrase in enumerate(PHRASES, 1)]
    listen_delays = step_listen(cable_in, cable_out, recognizer)

    print()
    measured = [d for d in delays if d is not None]
    if measured:
        print("Demora de cada frase: " + ", ".join(f"{d:.1f} s" for d in measured))
    if listen_delays:
        print("Demora de lo que te dicen: " + ", ".join(f"{d:.1f} s" for d in listen_delays))
    if FAILURES:
        print(f"FALLARON {len(FAILURES)} verificaciones:")
        for failure in FAILURES:
            print(f"  - {failure}")
        sys.exit(1)
    print("TODO OK con audio real")


if __name__ == "__main__":
    main()
