"""Grabación de la muestra de voz de referencia que se usa para clonar tu
timbre. Con 10-20 segundos hablando con normalidad es suficiente.

Se graba con toda la calidad del micrófono y se guarda a 24 kHz: la voz
natural copia también cómo suena la grabación, y a 16 kHz (calidad de
teléfono) tu voz clonada saldría más apagada.

Usa la misma captura que la traducción en vivo (mismo micrófono y mismos
diagnósticos) y muestra el medidor de nivel mientras graba. Si lo grabado es silencio, no se guarda: clonar una muestra muda
produce una voz rota o muda en la traducción.
"""
from __future__ import annotations

import math
import platform
import time
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

from .audio_devices import resolve_input_device
from .audio_io import AudioDeviceError, MicrophoneStream, diagnose_microphone
from .console import StatusLine, format_meter

SAVE_RATE = 24000


def record_voice_sample(output_path: Path, seconds: float, input_device: int | None) -> None:
    device, notes = resolve_input_device(input_device)
    for note in notes:
        print(note)

    mic = MicrophoneStream(device, keep_recording=True)
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
    audio, rate = mic.recording()
    if rate > SAVE_RATE:
        g = math.gcd(rate, SAVE_RATE)
        audio, rate = resample_poly(audio, SAVE_RATE // g, rate // g).astype(np.float32), SAVE_RATE
    sf.write(str(output_path), audio, rate)
