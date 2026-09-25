"""Prueba de la versión portable sin micrófono ni parlantes: la corre el CI
en un Windows limpio con el Python portable, después de `download-models`.

Usa como "tu voz" una frase en español dicha por Piper, la reconoce, la
traduce al inglés y la dice con esa voz clonada (motor liviano). Falla si
algún paso no produce resultado.
"""
from __future__ import annotations

import math
import time

import numpy as np
import soundfile as sf
import sounddevice as sd
import torch
from scipy.signal import resample_poly

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
    text = SpeechRecognizer(get_profile("low")).transcribe(
        resample_poly(audio, 16000 // g, rate // g).astype(np.float32), "es"
    )
    assert text, "Whisper no reconoció nada"
    _step(f"reconocimiento: {text!r}", t)

    t = time.perf_counter()
    english = Translator("spa_Latn", "eng_Latn").translate(text)
    assert english, "la traducción salió vacía"
    _step(f"traducción: {english!r}", t)

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
    print("TODO OK")


if __name__ == "__main__":
    main()
