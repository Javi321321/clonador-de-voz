"""Grabación de la muestra de voz de referencia que se usa para clonar tu
timbre. Con 10-20 segundos hablando con normalidad es suficiente para XTTS-v2.
"""
from __future__ import annotations

from pathlib import Path

import sounddevice as sd
import soundfile as sf

from .audio_devices import SAMPLE_RATE


def record_voice_sample(output_path: Path, seconds: float, input_device: int | None) -> None:
    print(f"Grabando {seconds:.0f} segundos de tu voz. Habla con normalidad, sin ruido de fondo...")
    audio = sd.rec(
        int(seconds * SAMPLE_RATE),
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="float32",
        device=input_device,
    )
    sd.wait()
    sf.write(str(output_path), audio, SAMPLE_RATE)
