"""Prueba de una videollamada de verdad, como en Meet, con una voz artificial
de cada lado (en Linux, con PulseAudio y Chromium):

    vos: voz artificial en español -> "mi_microfono" -> clonavoz -> "cable"
         -> Chrome A (tu navegador: su micrófono es "CABLE_Output", el cable)
    Chrome A <== videollamada WebRTC (Opus), como Meet ==> Chrome B
    la otra persona (Chrome B): voz artificial en inglés, después en portugués
    Chrome A la reproduce en sus parlantes ("parlantes_a") -> clonavoz
         ("llamada") -> tus auriculares ("auriculares")

Se verifica lo que escucha cada lado, transcribiéndolo con Whisper: lo que
recibe Chrome B tiene que ser tu frase traducida (primero al inglés y, cuando
te hablan en portugués, al portugués), y en tus auriculares tiene que sonar en
español lo que dijo B. También se mide la demora de cada dirección.

Google Meet en sí no se puede automatizar (hace falta una cuenta de Google para
crear la reunión y bloquea los navegadores automatizados), pero para clonavoz
es lo mismo: Chrome, WebRTC, el micrófono que se le da al navegador (el cable
virtual) y los parlantes donde suena la llamada.

Uso (con los modelos ya descargados: clonavoz download-models --languages es en pt):

    xvfb-run -a python portable/llamada_test.py CARPETA_DE_DATOS SALIDA

Necesita: pulseaudio (con el plugin de ALSA), Xvfb y `pip install playwright` con
`playwright install chromium` (u otro Chromium en la variable CHROMIUM). La
corre el CI en una máquina Linux de GitHub (.github/workflows/llamada-linux.yml).
Crea sus dispositivos virtuales en PulseAudio y los define para ALSA en
~/.asoundrc (lo deja como estaba al terminar).
"""
from __future__ import annotations

import base64
import http.server
import io
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

DATA = Path(sys.argv[1]).resolve()
OUT = Path(sys.argv[2]).resolve()
os.environ["CLONAVOZ_HOME"] = str(DATA)

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402
from scipy.signal import resample_poly  # noqa: E402

import clonavoz.asr  # noqa: E402,F401  (carga torch antes que ctranslate2, como clonavoz)
from clonavoz.piper_tts import PiperSynthesizer  # noqa: E402
from faster_whisper import WhisperModel  # noqa: E402

RATE = 16000
# El Chromium a usar: el de CHROMIUM, o si no, el que instaló Playwright.
CHROMIUM = os.environ.get("CHROMIUM") or None
SINKS = ("mi_voz", "cable", "parlantes_a", "parlantes_b", "auriculares")
ASOUNDRC = """# Dispositivos de la prueba de llamada de clonavoz (portable/llamada_test.py).
pcm.mi_microfono {
    type pulse
    device "mi_voz.monitor"
    hint { show on description "Mi microfono (voz artificial)" }
}
pcm.clonavoz_cable {
    type pulse
    device "cable"
    hint { show on description "clonavoz: cable virtual" }
}
pcm.llamada {
    type pulse
    device "parlantes_a.monitor"
    hint { show on description "Audio de la llamada" }
}
"""
ENROLL_TEXT = (
    "Hola, esta es una muestra de mi voz para el traductor. Hoy hace un día muy lindo, así que voy a salir "
    "a caminar un rato por el parque con mis amigos. Después vamos a tomar un café y a charlar tranquilos."
)
# Lo que dice cada uno, en orden: (quién, idioma, frase)
TURNS = [
    ("ellos", "en", "Hi! Thanks for joining the call. How is the weather in Buenos Aires today?"),
    ("vos", "es", "Hola, gracias por invitarme. Hoy hace calor y el cielo está despejado."),
    ("ellos", "pt", "Que bom! Eu moro em São Paulo e aqui está chovendo muito."),
    ("ellos", "pt", "Você gosta de futebol? Eu vou ao estádio no domingo."),
    ("vos", "es", "Me encanta el fútbol, el domingo lo voy a mirar por televisión."),
]
LANGUAGE_NAMES = {"en": "inglés", "pt": "portugués", "es": "español"}
FAILURES: list[str] = []

PAGE = """<!doctype html>
<meta charset="utf-8">
<title>Llamada de prueba</title>
<audio id="remote" autoplay></audio>
<script>
let pc, ctx, voice, recording = false, chunks = [];

async function setup(role) {
  pc = new RTCPeerConnection({iceServers: []});
  ctx = new AudioContext({sampleRate: 48000});
  await ctx.resume();
  let stream;
  if (role === 'A') {
    // Como Meet: el micrófono que Windows (acá, PulseAudio) le da al navegador
    stream = await navigator.mediaDevices.getUserMedia({audio: true});
  } else {
    // La otra persona: su voz artificial entra a la llamada como su micrófono
    voice = ctx.createMediaStreamDestination();
    stream = voice.stream;
  }
  stream.getTracks().forEach(track => pc.addTrack(track, stream));
  pc.ontrack = (event) => {
    const audio = document.getElementById('remote');
    audio.srcObject = event.streams[0];
    if (role === 'B') {
      audio.muted = true;  // B no lo escucha: lo graba
      const source = ctx.createMediaStreamSource(event.streams[0]);
      const tap = ctx.createScriptProcessor(4096, 1, 1);
      source.connect(tap);
      tap.connect(ctx.destination);
      tap.onaudioprocess = (e) => {
        if (recording) chunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));
      };
    }
  };
}

function gathered() {
  return new Promise(resolve => {
    if (pc.iceGatheringState === 'complete') return resolve();
    pc.onicegatheringstatechange = () => { if (pc.iceGatheringState === 'complete') resolve(); };
  });
}
async function offer() {
  await pc.setLocalDescription(await pc.createOffer());
  await gathered();
  return pc.localDescription.sdp;
}
async function answer(sdp) {
  await pc.setRemoteDescription({type: 'offer', sdp});
  await pc.setLocalDescription(await pc.createAnswer());
  await gathered();
  return pc.localDescription.sdp;
}
async function accept(sdp) { await pc.setRemoteDescription({type: 'answer', sdp}); }
function state() { return pc.connectionState; }

async function speak(b64) {
  const bytes = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
  const audio = await ctx.decodeAudioData(bytes.buffer);
  const source = ctx.createBufferSource();
  source.buffer = audio;
  source.connect(voice);
  source.start();
  return audio.duration;
}

function startRecording() { chunks = []; recording = true; }
function recentPeak(count) {
  let peak = 0;
  for (const c of chunks.slice(-count)) for (const v of c) peak = Math.max(peak, Math.abs(v));
  return peak;
}
function stopRecording() {
  recording = false;
  const total = chunks.reduce((n, c) => n + c.length, 0);
  const all = new Float32Array(total);
  let at = 0;
  for (const c of chunks) { all.set(c, at); at += c.length; }
  const bytes = new Uint8Array(all.buffer);
  let text = '';
  for (let i = 0; i < bytes.length; i += 0x8000) text += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return {rate: ctx.sampleRate, data: btoa(text)};
}
</script>
"""


def check(ok: bool, message: str) -> bool:
    print(f"  {'ok ' if ok else 'MAL'}  {message}", flush=True)
    if not ok:
        FAILURES.append(message)
    return ok


def words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-záéíóúüñãõâêôçà']+", text.lower()) if len(w) >= 3}


def overlap(expected: str, heard: str) -> float:
    """Qué parte de las palabras de `expected` aparecen en `heard` (0 a 1)."""
    wanted = words(expected)
    return len(wanted & words(heard)) / len(wanted) if wanted else 0.0


def resample(audio: np.ndarray, rate: int, target: int = RATE) -> np.ndarray:
    g = np.gcd(rate, target)
    return resample_poly(audio, target // g, rate // g).astype(np.float32)


def first_sound(audio: np.ndarray, rate: int, since: float, threshold_db: float = -45.0) -> float | None:
    """Segundo en que empieza a sonar algo después de `since`."""
    hop = rate // 50
    start = max(0, int(since * rate))
    n = (len(audio) - start) // hop
    if n <= 0:
        return None
    loud = np.abs(audio[start : start + n * hop]).reshape(n, hop).max(axis=1) > 10 ** (threshold_db / 20)
    found = np.flatnonzero(loud)
    return since + found[0] / 50 if len(found) else None


class Verifier:
    """Transcribe lo que escuchó cada lado, para compararlo con lo que tenía que escuchar."""

    def __init__(self) -> None:
        self._model = WhisperModel("base", device="cpu", compute_type="int8")

    def transcribe(self, audio: np.ndarray, language: str) -> str:
        segments, _ = self._model.transcribe(audio, language=language, beam_size=5)
        return " ".join(segment.text.strip() for segment in segments).strip()


def wav_bytes(audio: np.ndarray, rate: int) -> bytes:
    buffer = io.BytesIO()
    sf.write(buffer, audio, rate, format="WAV", subtype="PCM_16")
    return buffer.getvalue()


# --- El audio: dispositivos virtuales de PulseAudio -------------------------


def run(*command: str) -> str:
    return subprocess.run(command, check=True, capture_output=True, text=True).stdout


def setup_audio() -> str | None:
    """Arranca PulseAudio con los dispositivos de la prueba. Devuelve el
    ~/.asoundrc que había antes (para dejarlo como estaba)."""
    if subprocess.run(["pulseaudio", "--check"]).returncode != 0:
        subprocess.run(["pulseaudio", "-D", "--exit-idle-time=-1"], check=True)
        time.sleep(2)
    existing = run("pactl", "list", "short", "sinks")
    for sink in SINKS:
        if f"\t{sink}\t" not in existing:
            run("pactl", "load-module", "module-null-sink", f"sink_name={sink}",
                f"sink_properties=device.description={sink}")
    # El cable visto como micrófono, como "CABLE Output" en Windows (Chrome no
    # ofrece los "monitores" de PulseAudio como micrófonos).
    if "cable_output" not in run("pactl", "list", "short", "sources"):
        run("pactl", "load-module", "module-remap-source", "master=cable.monitor", "source_name=cable_output",
            "source_properties=device.description=CABLE_Output")
    run("pactl", "set-default-sink", "auriculares")  # la salida predeterminada: tus auriculares
    # El micrófono predeterminado es el cable, como lo deja ACTIVAR en Windows: así
    # tu navegador (como Meet) lo usa sin elegir nada. Chrome toma el predeterminado
    # del sistema, no el que se le indica en PULSE_SOURCE.
    run("pactl", "set-default-source", "cable_output")
    asoundrc = Path.home() / ".asoundrc"
    before = asoundrc.read_text() if asoundrc.exists() else None
    asoundrc.write_text(ASOUNDRC)
    return before


def restore_asoundrc(before: str | None) -> None:
    asoundrc = Path.home() / ".asoundrc"
    if before is None:
        asoundrc.unlink(missing_ok=True)
    else:
        asoundrc.write_text(before)


class Recorder:
    """Graba un monitor de PulseAudio (lo que suena en un dispositivo)."""

    def __init__(self, source: str) -> None:
        self._process = subprocess.Popen(
            ["parec", "-d", source, "--format=float32le", f"--rate={RATE}", "--channels=1", "--raw",
             "--latency-msec=20"],
            stdout=subprocess.PIPE,
        )
        self.started = time.monotonic()
        self._data = bytearray()
        self._thread = threading.Thread(target=self._read, daemon=True)
        self._thread.start()

    def _read(self) -> None:
        while True:
            block = self._process.stdout.read(3200)
            if not block:
                return
            self._data.extend(block)

    def audio(self) -> np.ndarray:
        data = bytes(self._data)
        return np.frombuffer(data[: len(data) // 4 * 4], dtype=np.float32).copy()

    def seconds(self, when: float) -> float:
        return when - self.started

    def close(self) -> None:
        self._process.terminate()
        self._process.wait(timeout=10)


def play(sink: str, audio: np.ndarray, rate: int) -> None:
    """Reproduce (y espera a que termine) en un dispositivo de PulseAudio."""
    path = OUT / "_frase.wav"
    sf.write(str(path), audio, rate)
    subprocess.run(["paplay", f"--device={sink}", str(path)], check=True)


# --- clonavoz ----------------------------------------------------------------


class Clonavoz:
    """`clonavoz <args>` mostrando y guardando lo que escribe."""

    def __init__(self, *args: str) -> None:
        env = dict(os.environ, PYTHONUNBUFFERED="1", HF_HUB_OFFLINE="1")
        self.process = subprocess.Popen(
            [sys.executable, "-m", "clonavoz", *args],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", env=env,
        )
        self.lines: list[tuple[float, str]] = []
        self._thread = threading.Thread(target=self._read, daemon=True)
        self._thread.start()

    def _read(self) -> None:
        for line in self.process.stdout:
            line = line.rstrip("\n")
            self.lines.append((time.monotonic(), line))
            print(f"    | {line}", flush=True)

    def matches(self, pattern: str, since: int = 0) -> list[re.Match]:
        regex = re.compile(pattern)
        return [m for _, line in self.lines[since:] for m in [regex.search(line)] if m]

    def wait_for(self, pattern: str, since: int = 0, timeout: float = 120) -> tuple[int, float, re.Match] | None:
        """Espera una línea que cumpla `pattern` (a partir de la línea `since`)."""
        end = time.monotonic() + timeout
        regex = re.compile(pattern)
        while time.monotonic() < end:
            for i in range(since, len(self.lines)):
                match = regex.search(self.lines[i][1])
                if match:
                    return i, self.lines[i][0], match
            if self.process.poll() is not None and not self._thread.is_alive():
                return None
            time.sleep(0.1)
        return None

    def stop(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.process.kill()


def enroll(voice: PiperSynthesizer) -> None:
    """Graba "tu" muestra de voz como lo hace el usuario: `clonavoz enroll` desde el micrófono."""
    print("\n== Tu voz: clonavoz enroll desde el micrófono ==", flush=True)
    audio, rate = voice.synthesize(ENROLL_TEXT, "es")
    proc = Clonavoz("enroll", "--seconds", "15", "--input-device", "mi_microfono")
    started = proc.wait_for(r"Grabando", timeout=120)
    if check(started is not None, "enroll empieza a grabar desde 'mi_microfono' (elegido por su nombre)"):
        play("mi_voz", audio, rate)
    proc.process.wait(timeout=120)
    check(proc.process.returncode == 0 and (DATA / "mi_voz.wav").exists(), "enroll guardó tu muestra de voz")


# --- La videollamada -----------------------------------------------------------


class Page(http.server.SimpleHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - así se llama en http.server
        body = PAGE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        pass


def start_call(playwright):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Page)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    args = [
        "--use-fake-ui-for-media-stream",  # como tocar "Permitir" el micrófono
        "--autoplay-policy=no-user-gesture-required",
        "--disable-features=WebRtcHideLocalIpsWithMdns",
    ]
    browsers, pages = [], []
    for role, sink in (("A", "parlantes_a"), ("B", "parlantes_b")):
        env = dict(os.environ, PULSE_SINK=sink)  # dónde suena cada navegador
        browser = playwright.chromium.launch(executable_path=CHROMIUM, headless=False, args=args, env=env)
        page = browser.new_page()
        page.on("console", lambda message, role=role: print(f"    [{role}] {message.text}", flush=True))
        page.goto(url)
        page.evaluate("role => setup(role)", role)
        browsers.append(browser)
        pages.append(page)
    a, b = pages
    sdp = a.evaluate("() => offer()")
    sdp = b.evaluate("sdp => answer(sdp)", sdp)
    a.evaluate("sdp => accept(sdp)", sdp)
    end = time.monotonic() + 30
    while time.monotonic() < end and not (a.evaluate("() => state()") == b.evaluate("() => state()") == "connected"):
        time.sleep(0.2)
    return server, browsers, a, b


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    before = setup_audio()
    from playwright.sync_api import sync_playwright

    import sounddevice as sd

    names = [device["name"] for device in sd.query_devices()]
    call_device = names.index("llamada")
    whisper = Verifier()
    me = PiperSynthesizer(voices={"es": "es_ES-davefx-medium"})
    them = PiperSynthesizer(voices={"en": "en_US-ljspeech-medium", "pt": "pt_BR-faber-medium"})
    heard = Recorder("auriculares.monitor")  # lo que escuchás vos
    clonavoz = None
    try:
        enroll(me)
        print("\n== La videollamada: Chrome A (vos) y Chrome B (la otra persona), por WebRTC ==", flush=True)
        with sync_playwright() as playwright:
            server, browsers, a, b = start_call(playwright)
            check(a.evaluate("() => state()") == "connected", "los dos navegadores están en la llamada (WebRTC)")
            clonavoz = Clonavoz(
                "conversar", "--source-lang", "es", "--target-lang", "auto", "--listen-lang", "es",
                "--their-langs", "en", "pt", "--their-voice", "parecida",
                "--input-device", "mi_microfono", "--call-device", str(call_device),
            )
            ready = clonavoz.wait_for(r"^Listo: vos hablás", timeout=600)
            if not check(ready is not None, "clonavoz conversar arranca"):
                return
            time.sleep(2)
            results = []
            for number, (who, code, phrase) in enumerate(TURNS, 1):
                results.append(turn(number, who, code, phrase, clonavoz, a, b, heard, whisper, me, them))
            for browser in browsers:
                browser.close()
            server.shutdown()
        report(results, heard)
    finally:
        if clonavoz is not None:
            clonavoz.stop()
            (OUT / "clonavoz.log").write_text("\n".join(line for _, line in clonavoz.lines) + "\n", encoding="utf-8")
        heard.close()
        sf.write(str(OUT / "lo_que_escuchaste_vos.wav"), heard.audio(), RATE)
        restore_asoundrc(before)
        (OUT / "_frase.wav").unlink(missing_ok=True)


def turn(number, who, code, phrase, clonavoz, a, b, heard, whisper, me, them) -> dict:
    since = len(clonavoz.lines)
    result = {"número": number, "quién": who, "idioma": code, "frase": phrase}
    if who == "ellos":
        return their_turn(result, since, clonavoz, b, heard, whisper, them)
    return my_turn(result, since, clonavoz, b, whisper, me)


def their_turn(result, since, clonavoz, b, heard, whisper, them) -> dict:
    """La otra persona habla (en Chrome B): lo tenés que ver y escuchar en español."""
    number, code, phrase = result["número"], result["idioma"], result["frase"]
    name = LANGUAGE_NAMES[code]
    print(f"\n== {number}) La otra persona dice en {name}: {phrase!r} ==", flush=True)
    audio, rate = them.synthesize(phrase, code)
    sf.write(str(OUT / f"{number}_la_otra_persona_dice_{code}.wav"), audio, rate)
    spoke = time.monotonic()
    seconds = b.evaluate("b64 => speak(b64)", base64.b64encode(wav_bytes(audio, rate)).decode())
    finished = spoke + seconds
    said = clonavoz.wait_for(r"^  Te dicen \((\w+)\) > (.*)", since=since, timeout=120)
    if not check(said is not None, "clonavoz muestra lo que te dijeron"):
        return result
    check(said[2].group(1) == name, f"reconoce que te hablan en {name} (dijo: {said[2].group(1)})")
    if not check(clonavoz.wait_for(r"^ {10}es > ", since=said[0], timeout=60) is not None,
                 "muestra la traducción al español"):
        return result
    # Esperar a que la traducción termine de sonar en tus auriculares (puede empezar
    # antes de que termine de hablar: clonavoz traduce cada oración apenas la dice).
    start = None
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        audio_heard = heard.audio()
        start = first_sound(audio_heard, RATE, heard.seconds(spoke))
        if start is not None and first_sound(audio_heard, RATE, len(audio_heard) / RATE - 1.5) is None:
            break
        time.sleep(0.3)
    result["entendido"] = " ".join(m.group(2) for m in clonavoz.matches(r"^  Te dicen \((\w+)\) > (.*)", since))
    result["traducción"] = " ".join(m.group(1) for m in clonavoz.matches(r"^ {10}es > (.*)", since))
    understood = overlap(phrase, result["entendido"])
    check(understood >= 0.5, f"entiende lo que te dijeron ({understood:.0%} de las palabras): {result['entendido']!r}")
    if check(start is not None, "la traducción suena en tus auriculares"):
        result["demora"] = start - heard.seconds(finished)
        audio_heard = heard.audio()[int(start * RATE) :]
        sf.write(str(OUT / f"{number}_escuchaste_en_espanol.wav"), audio_heard, RATE)
        text = whisper.transcribe(audio_heard, "es")
        result["se escuchó"] = text
        match = overlap(result["traducción"], text)
        check(match >= 0.5, f"en tus auriculares se entiende la traducción ({match:.0%}): {text!r}")
        when = "antes" if result["demora"] < 0 else "después"
        print(f"  empezó a sonar {abs(result['demora']):.1f} s {when} de que terminó de hablar", flush=True)
    for m in clonavoz.matches(r"(Te hablan en .*)", since):
        print(f"  clonavoz: {m.group(1)}", flush=True)
    return result


def my_turn(result, since, clonavoz, b, whisper, me) -> dict:
    """Hablás vos (en tu micrófono): la otra persona tiene que escucharte traducido."""
    number, phrase = result["número"], result["frase"]
    target = expected_target(clonavoz)
    print(f"\n== {number}) Vos decís: {phrase!r} (te tienen que escuchar en {LANGUAGE_NAMES[target]}) ==", flush=True)
    audio, rate = me.synthesize(phrase, "es")
    sf.write(str(OUT / f"{number}_vos_decis_es.wav"), audio, rate)
    b.evaluate("() => startRecording()")
    recording_started = time.monotonic()
    play("mi_voz", audio, rate)
    finished = time.monotonic() - recording_started
    said = clonavoz.wait_for(r"^  Vos \(es\) > (.*)", since=since, timeout=120)
    if not check(said is not None, "clonavoz entiende lo que dijiste"):
        b.evaluate("() => stopRecording()")
        return result
    translated = clonavoz.wait_for(r"^ {10}(\w+) > (.*)", since=said[0], timeout=60)
    if not check(translated is not None, "y lo traduce"):
        b.evaluate("() => stopRecording()")
        return result
    # Esperar a que tu traducción termine de llegar por la llamada (1.5 s sin sonido).
    deadline = time.monotonic() + 60
    started = False
    while time.monotonic() < deadline:
        peak = b.evaluate("() => recentPeak(18)")  # ~1.5 s
        started = started or peak > 0.005
        if started and peak <= 0.005:
            break
        time.sleep(0.3)
    received = b.evaluate("() => stopRecording()")
    heard = np.frombuffer(base64.b64decode(received["data"]), dtype=np.float32)
    sf.write(str(OUT / f"{number}_la_otra_persona_escucha.wav"), heard, int(received["rate"]))
    heard = resample(heard, int(received["rate"]))
    result["entendido"] = " ".join(m.group(1) for m in clonavoz.matches(r"^  Vos \(es\) > (.*)", since))
    pieces = clonavoz.matches(r"^ {10}(\w+) > (.*)", since)
    result["traducción"] = " ".join(m.group(2) for m in pieces)
    understood = overlap(phrase, result["entendido"])
    check(understood >= 0.5, f"entiende lo que dijiste ({understood:.0%}): {result['entendido']!r}")
    languages = sorted({m.group(1) for m in pieces})
    check(languages == [target], f"te traduce al {LANGUAGE_NAMES[target]} (tradujo a {', '.join(languages)})")
    start = first_sound(heard, RATE, 0.0)
    if check(start is not None, "tu voz traducida le llega a la otra persona por la llamada"):
        result["demora"] = start - finished
        text = whisper.transcribe(heard[int(start * RATE) :], target)
        result["se escuchó"] = text
        match = overlap(result["traducción"], text)
        check(match >= 0.5, f"la otra persona entiende tu traducción ({match:.0%}): {text!r}")
        when = "antes" if result["demora"] < 0 else "después"
        print(f"  empezó a llegarle {abs(result['demora']):.1f} s {when} de que terminaste de hablar", flush=True)
    return result


def expected_target(clonavoz: Clonavoz) -> str:
    """En qué idioma te tienen que escuchar ahora (auto: el último en que te hablaron)."""
    target = "en"
    for _, line in clonavoz.lines:
        match = re.search(r"desde ahora te escuchan en (\w+)", line)
        if match:
            target = {name: code for code, name in LANGUAGE_NAMES.items()}.get(match.group(1), target)
    return target


def report(results: list[dict], heard: Recorder) -> None:
    lines = ["Prueba de videollamada (WebRTC, como Meet) con clonavoz", ""]
    for r in results:
        who = "La otra persona" if r["quién"] == "ellos" else "Vos"
        lines.append(f"{r['número']}) {who} ({LANGUAGE_NAMES[r['idioma']]}): {r['frase']}")
        for key in ("entendido", "traducción", "se escuchó"):
            if key in r:
                lines.append(f"     {key}: {r[key]}")
        if "demora" in r:
            when = "antes" if r["demora"] < 0 else "después"
            lines.append(f"     empezó a sonar {abs(r['demora']):.1f} s {when} de que terminó la frase")
        lines.append("")
    verdict = "TODO BIEN" if not FAILURES else f"{len(FAILURES)} problemas: " + "; ".join(FAILURES)
    lines.append("Resultado: " + verdict)
    (OUT / "resultado.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n" + "\n".join(lines), flush=True)


if __name__ == "__main__":
    main()
    sys.exit(1 if FAILURES else 0)
