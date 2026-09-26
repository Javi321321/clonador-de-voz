"""Prueba de la versión portable sin micrófono ni parlantes: la corre el CI
en un Windows limpio con el Python portable, después de `download-models`.

Usa como "tu voz" una frase en español dicha por Piper, la reconoce, la
traduce al inglés y la dice con esa voz clonada (motor liviano y voz
natural). Falla si algún paso no produce resultado.
"""
from __future__ import annotations

import math
import re
import time

import numpy as np
import soundfile as sf
import sounddevice as sd
import torch
from scipy.signal import resample_poly

from clonavoz import parakeet_asr
from clonavoz.asr import SpeechRecognizer
from clonavoz.config import get_profile
from clonavoz.languages import get_language
from clonavoz.paths import data_dir
from clonavoz.piper_tts import PiperSynthesizer
from clonavoz.translate import Translator
from clonavoz.vad import StreamingVAD
from clonavoz.voice_clone import VoiceSynthesizer


def _step(name: str, started: float) -> None:
    print(f"  ok  {name} ({time.perf_counter() - started:.1f}s)", flush=True)


def main() -> None:
    print(f"PortAudio: {sd.get_portaudio_version()[1]} | dispositivos de audio: {len(sd.query_devices())}")
    profile = get_profile("auto")
    print(f"Perfil detectado: {profile.name} | motor de voz: {profile.voice_engine} | hilos: {torch.get_num_threads()}")

    t = time.perf_counter()
    data_dir().mkdir(parents=True, exist_ok=True)
    audio, rate = PiperSynthesizer(speaker_pitch_hz=110).synthesize(
        "Hola, esta es mi voz. Estoy probando el traductor en otra computadora.", "es"
    )
    sample = data_dir() / "mi_voz.wav"
    sf.write(str(sample), audio, rate)
    _step(f"muestra de voz de prueba ({len(audio) / rate:.1f}s)", t)

    t = time.perf_counter()
    threads = torch.get_num_threads()
    StreamingVAD()
    assert torch.get_num_threads() == threads, "el VAD dejó a torch con menos hilos"
    _step("detector de voz (sin internet)", t)

    t = time.perf_counter()
    g = math.gcd(rate, 16000)
    recognizer = SpeechRecognizer(get_profile("low"), "es")
    if parakeet_asr.ready() and parakeet_asr.enough_memory():
        assert recognizer.name == "Parakeet", f"con Parakeet descargado se usó {recognizer.name}"
    audio16 = resample_poly(audio, 16000 // g, rate // g).astype(np.float32)
    text = recognizer.transcribe(audio16, "es")
    assert text, f"{recognizer.name} no reconoció nada"
    _step(f"reconocimiento ({recognizer.name}): {text!r}", t)

    # Whisper con la ventana corta (la que se usa en PCs lentas o con poca memoria).
    t = time.perf_counter()
    whisper = SpeechRecognizer(get_profile("low"))
    whisper.transcribe(audio16[:16000], "es")
    t = time.perf_counter()
    short = whisper._transcribe_short(audio16, "es")
    assert short is not None, "Whisper con la ventana corta no dio un resultado confiable"
    heard = set(re.findall(r"\w+", short.lower()))
    assert len(heard & {"hola", "esta", "es", "mi", "voz", "estoy", "probando", "traductor", "computadora"}) >= 4, short
    _step(f"reconocimiento rápido ({whisper.name}, ventana de 10 s): {short!r}", t)
    del whisper

    # Vosk: el que va entendiendo mientras hablás (el de las PCs lentas).
    from clonavoz import vosk_asr
    from clonavoz.punctuation import restore

    assert vosk_asr.ready("es"), "falta el reconocimiento en streaming (Vosk): la descarga falló"
    t = time.perf_counter()
    vosk = SpeechRecognizer(get_profile("low"), "es", parakeet=False)
    assert vosk.name == "Vosk" and vosk.streaming, vosk.name
    stream = vosk.stream()
    for start in range(0, len(audio16), 512):
        stream.accept(audio16[start : start + 512])
    heard = stream.text()
    assert len(set(heard.split()) & {"hola", "esta", "es", "mi", "voz", "estoy", "probando", "traductor"}) >= 4, heard
    _step(f"reconocimiento en streaming (Vosk): {restore(heard, 'es')!r}", t)
    del vosk

    t = time.perf_counter()
    translator = Translator("spa_Latn", "eng_Latn", pair=("es", "en"))
    english = translator.translate(text)
    assert english, "la traducción salió vacía"
    _step(f"traducción ({translator.name}): {english!r}", t)

    t = time.perf_counter()
    synth = VoiceSynthesizer(get_profile("low"), sample, engine="openvoice")
    synth.preload(get_language("en"))
    _step("carga del motor de voz liviano", t)

    t = time.perf_counter()
    out, out_rate = synth.synthesize(english, get_language("en"))
    seconds = len(out) / out_rate
    assert seconds > 1.0 and np.abs(out).max() > 0.01, "la voz clonada salió vacía"
    sf.write(str(data_dir() / "prueba_en.wav"), out, out_rate)
    _step(f"voz clonada en inglés ({seconds:.1f}s de audio)", t)

    t = time.perf_counter()
    fast = VoiceSynthesizer(get_profile("low"), sample, engine="rapida")
    fast.preload(get_language("en"))
    out, out_rate = fast.synthesize(english, get_language("en"))
    assert len(out) / out_rate > 1.0 and np.abs(out).max() > 0.01, "la voz rápida salió vacía"
    _step(f"voz rápida en inglés ({len(out) / out_rate:.1f}s de audio)", t)

    _what_they_say()
    _natural_voice(sample, english)
    print("TODO OK")


def _what_they_say() -> None:
    """Lo que te dicen: portugués por los traductores rápidos, el idioma
    reconocido solo y la traducción dicha con una voz parecida."""
    from clonavoz import listen
    from clonavoz.vad import Utterance

    t = time.perf_counter()
    for source, target, text in (
        ("es", "pt", "Hola, ¿cómo estás? Te quería contar algo."),
        ("pt", "es", "Oi, tudo bem? Você pode me mandar o contrato até amanhã?"),
        ("en", "pt", "Can we move the meeting to Thursday?"),
        ("pt", "en", "Você já almoçou?"),
    ):
        translator = Translator(get_language(source).nllb_code, get_language(target).nllb_code, pair=(source, target))
        assert translator.name.startswith("Opus-MT"), f"{source}->{target} sin el traductor rápido: {translator.name}"
        out = translator.translate(text)
        assert out, f"{source}->{target}: la traducción salió vacía"
        print(f"      {translator.name}: {text!r} -> {out!r}")
    _step("traductores rápidos con portugués", t)

    t = time.perf_counter()
    pipeline = listen.IncomingPipeline(get_profile("low"), "es", on_message=print)
    _step(f"lo que te dicen: cargado ({pipeline.asr_name})", t)
    for code, phrase in (
        ("en", "Hi! How are you doing today? I wanted to ask you about the meeting."),
        ("pt", "Oi, tudo bem? Você pode me mandar o contrato até amanhã?"),
    ):
        voice, voice_rate = PiperSynthesizer(speaker_pitch_hz=200).synthesize(phrase, code)
        g = math.gcd(voice_rate, 16000)
        audio16 = resample_poly(voice, 16000 // g, voice_rate // g).astype(np.float32)
        heard: list[tuple] = []
        pipeline.on_transcript = lambda language, text, translated, heard=heard: heard.append(
            (language, text, translated)
        )
        t = time.perf_counter()
        pipeline._process(Utterance(audio16))
        assert heard and heard[0][0] == code, f"no reconoció el idioma ({code}): {heard}"
        assert heard[0][2], f"no tradujo lo que dijo en {code}: {heard}"
        spoken = []
        while not pipeline._audio_out_queue.empty():
            item = pipeline._audio_out_queue.get()
            spoken.append(len(item[1]) / item[2] if item[0] == "audio" else 0.0)
        assert sum(spoken) > 0.5, f"la traducción de lo que dijo en {code} no se dijo"
        _step(f"te dicen ({code}) {heard[0][1]!r} -> {heard[0][2]!r} ({sum(spoken):.1f}s de voz)", t)


def _natural_voice(sample, english: str) -> None:
    """Voz natural (Pocket TTS). Con los pesos que clonan voces (bajados con el
    token de Hugging Face, si el CI lo tiene) se prueba con la voz de prueba; si
    no, al menos que Pocket TTS funcione en Windows, con una voz prediseñada."""
    from clonavoz import pocket_voice

    t = time.perf_counter()
    if pocket_voice.cloning_ready("en"):
        voice = pocket_voice.PocketVoice(sample)
        voice.preload("en")
        _step("voz natural: modelo y tu voz cargados", t)
        t = time.perf_counter()
        out, rate = voice.synthesize(english, "en")
        what = "voz natural clonada"
    else:
        print("  --  voz natural: sin el modelo que clona voces (falta el token); se prueba Pocket TTS "
              "con una voz prediseñada", flush=True)
        tts_model, _ = pocket_voice._import_pocket()
        model = tts_model.load_model(language="english")
        state = model.get_state_for_audio_prompt("alba")
        _step("Pocket TTS cargado", t)
        t = time.perf_counter()
        out, rate = model.generate_audio(state, english).squeeze(0).numpy(), model.sample_rate
        what = "Pocket TTS"
    elapsed, seconds = time.perf_counter() - t, len(out) / rate
    assert seconds > 1.0 and np.abs(out).max() > 0.01, f"{what}: salió vacía"
    sf.write(str(data_dir() / "prueba_natural_en.wav"), out, rate)
    _step(f"{what} en inglés ({seconds:.1f}s de audio, {elapsed / seconds:.2f}x del tiempo real, "
          f"{torch.get_num_threads()} hilos)", t)
    _natural_voice_int8(english)


def _natural_voice_int8(english: str) -> None:
    """La versión int8 de la voz natural (la que se usa en procesadores con AVX2)."""
    import warnings

    from clonavoz import pocket_voice

    if not pocket_voice.int8_supported():
        print("  --  voz natural int8: este procesador no tiene AVX2 (se usa la normal)", flush=True)
        return
    t = time.perf_counter()
    with warnings.catch_warnings(record=True) as shown:
        warnings.simplefilter("always")
        model = pocket_voice.load_model(True, language="english")
    assert not shown, f"la voz int8 muestra avisos técnicos: {[str(w.message)[:80] for w in shown]}"
    state = model.get_state_for_audio_prompt("alba")
    model.generate_audio(state, "Hi.")
    t = time.perf_counter()
    out, rate = model.generate_audio(state, english).squeeze(0).numpy(), model.sample_rate
    elapsed, seconds = time.perf_counter() - t, len(out) / rate
    assert seconds > 1.0 and np.abs(out).max() > 0.01, "la voz natural int8 salió vacía"
    _step(f"voz natural int8 ({seconds:.1f}s de audio, {elapsed / seconds:.2f}x del tiempo real)", t)


if __name__ == "__main__":
    main()
