"""Grabación de la muestra de voz de referencia que se usa para clonar tu
timbre. Con 10-20 segundos hablando con normalidad es suficiente para XTTS-v2.

Usa la misma captura que la traducción en vivo (mismo micrófono, misma
adaptación de frecuencia de muestreo) y muestra el medidor de nivel mientras
graba. Si lo grabado es silencio, no se guarda: clonar una muestra muda
produce una voz rota o muda en la traducción.
"""
from __future__ import annotations

import platform
import time
from pathlib import Path

import numpy as np
import soundfile as sf

from .audio_devices import SAMPLE_RATE, resolve_input_device
from .audio_io import AudioDeviceError, MicrophoneStream, diagnose_microphone
from .console import StatusLine, format_meter


def record_voice_sample(output_path: Path, seconds: float, input_device: int | None) -> None:
    device, notes = resolve_input_device(input_device)
    for note in notes:
        print(note)

    chunks: list[np.ndarray] = []
    mic = MicrophoneStream(device, on_frame=chunks.append)
    mic.start()
    print(f"Micrófono: {mic.description}")
    print(f"Grabando {seconds:.0f} segundos de tu voz. Habla con normalidad, sin ruido de fondo...")
    status = StatusLine()
    end = time.monotonic() + seconds
    try:
        while (remaining := end - time.monotonic()) > 0:
            status.update(f"Mic {format_meter(mic.pop_level_db())}  quedan {remaining:4.1f} s")
            time.sleep(min(0.1, remaining))
    finally:
        mic.close()
        status.clear()

    problems = diagnose_microphone(mic.stats, windows=platform.system() == "Windows")
    if problems:
        raise AudioDeviceError("No se guardó la muestra de voz:\n  " + "\n  ".join(problems))
    audio = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)
    sf.write(str(output_path), audio, SAMPLE_RATE)
