"""Utilidades de dispositivos de audio multiplataforma: listar micrófonos y
detectar el micrófono virtual (VB-CABLE en Windows, BlackHole en macOS, o el
sink de PulseAudio/PipeWire en Linux) que después seleccionas como entrada
de audio en Zoom, Meet, Teams, Discord o cualquier otra app.
"""
from __future__ import annotations

import platform
from dataclasses import dataclass

import sounddevice as sd

SAMPLE_RATE = 16000

_VIRTUAL_DEVICE_HINTS = {
    "Windows": ["cable input", "vb-audio", "voicemeeter"],
    "Darwin": ["blackhole"],
    "Linux": ["clonavoz", "null sink"],
}


@dataclass
class AudioDevice:
    index: int
    name: str
    max_input_channels: int
    max_output_channels: int


def list_devices() -> list[AudioDevice]:
    devices = []
    for idx, dev in enumerate(sd.query_devices()):
        devices.append(
            AudioDevice(
                index=idx,
                name=dev["name"],
                max_input_channels=dev["max_input_channels"],
                max_output_channels=dev["max_output_channels"],
            )
        )
    return devices


def find_virtual_output_device() -> AudioDevice | None:
    """Busca por nombre un dispositivo de salida que sea un micrófono
    virtual ya instalado, para usarlo como salida del audio traducido."""
    hints = _VIRTUAL_DEVICE_HINTS.get(platform.system(), [])
    for dev in list_devices():
        if dev.max_output_channels <= 0:
            continue
        lowered = dev.name.lower()
        if any(hint in lowered for hint in hints):
            return dev
    return None


def print_devices() -> None:
    for dev in list_devices():
        kinds = []
        if dev.max_input_channels > 0:
            kinds.append(f"entrada:{dev.max_input_channels}ch")
        if dev.max_output_channels > 0:
            kinds.append(f"salida:{dev.max_output_channels}ch")
        print(f"[{dev.index:2d}] {dev.name}  ({', '.join(kinds)})")
