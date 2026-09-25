"""Utilidades de dispositivos de audio multiplataforma: listar micrófonos y
detectar el micrófono virtual (VB-CABLE en Windows, BlackHole en macOS, o el
sink de PulseAudio/PipeWire en Linux) que después seleccionas como entrada
de audio en Zoom, Meet, Teams, Discord o cualquier otra app.

Un cable virtual tiene dos puntas: clonavoz *reproduce* la traducción en la
punta de salida (ej. "CABLE Input") y la app de videollamada la *graba* desde
la punta de entrada (ej. "CABLE Output"). Esa punta de entrada nunca debe ser
el micrófono de clonavoz: desde ahí no se escucha tu voz, solo silencio (o la
propia traducción). Windows suele dejarla como micrófono predeterminado al
instalar VB-CABLE, por eso `resolve_input_device` la detecta y la evita.
"""
from __future__ import annotations

import platform
import re
from dataclasses import dataclass

import sounddevice as sd

SAMPLE_RATE = 16000

# Punta de salida del cable virtual (donde clonavoz escribe), en orden de
# preferencia: si hay VB-CABLE y además Voicemeeter, se prefiere el cable.
_VIRTUAL_DEVICE_HINTS = {
    "Windows": ["cable input", "cable in ", "vb-audio", "voicemeeter"],
    "Darwin": ["blackhole"],
    "Linux": ["clonavoz", "null sink"],
}

# Punta de entrada de los cables virtuales (lo que usa la app de videollamada
# como micrófono). "CABLE Output" de VB-CABLE se detecta aparte (ver
# `is_virtual_mic_input`) porque también existen "CABLE-A Output", etc.
_VIRTUAL_INPUT_HINTS = ("blackhole", "soundflower", "clonavoz", "monitor of")

# Entradas que no son un micrófono real: el "mapeador" de Windows (que apunta
# al predeterminado, o sea al propio cable virtual; su nombre cambia según el
# idioma de Windows) y las mezclas estéreo.
_NOT_A_MICROPHONE = (
    "mapp", "mapea", "asignador", "primary", "primari", "stereo mix", "mezcla est", "what u hear", "loopback",
    "monitor",
)
_MICROPHONE_WORDS = ("mic", "headset", "auricular")


@dataclass
class AudioDevice:
    index: int
    name: str
    max_input_channels: int
    max_output_channels: int
    hostapi: int = 0


def list_devices() -> list[AudioDevice]:
    devices = []
    for idx, dev in enumerate(sd.query_devices()):
        devices.append(
            AudioDevice(
                index=idx,
                name=dev["name"],
                max_input_channels=dev["max_input_channels"],
                max_output_channels=dev["max_output_channels"],
                hostapi=dev["hostapi"],
            )
        )
    return devices


def describe_device(index: int) -> str:
    dev = sd.query_devices(index)
    hostapi = sd.query_hostapis(dev["hostapi"])["name"]
    return f"[{index}] {dev['name']} ({hostapi})"


def is_virtual_mic_input(name: str) -> bool:
    """True si `name` es la punta "micrófono" de un cable virtual (lo que
    graba la app de videollamada), y no un micrófono real."""
    lowered = name.lower()
    if "cable" in lowered and "output" in lowered:
        return True
    return any(hint in lowered for hint in _VIRTUAL_INPUT_HINTS)


def virtual_mic_name(output_device_name: str) -> str | None:
    """Nombre del micrófono que hay que elegir en Zoom/Meet/Teams/Discord para
    recibir lo que clonavoz reproduce en `output_device_name`."""
    base = output_device_name.split(" (")[0].strip()
    lowered = base.lower()
    if lowered.startswith("cable"):
        # VB-CABLE: "CABLE Input" / "CABLE In 16ch" -> "CABLE Output".
        return re.sub(r"\s*in(put)?\b.*$", " Output", base, flags=re.IGNORECASE)
    if "blackhole" in lowered:
        return base
    if "clonavoz" in lowered:
        return "Monitor of ClonaVoz_Mic"
    return None


def find_virtual_output_device() -> AudioDevice | None:
    """Busca por nombre un dispositivo de salida que sea un micrófono
    virtual ya instalado, para usarlo como salida del audio traducido."""
    hints = _VIRTUAL_DEVICE_HINTS.get(platform.system(), [])
    outputs = [dev for dev in list_devices() if dev.max_output_channels > 0]
    for hint in hints:
        for dev in outputs:
            if hint in dev.name.lower():
                return dev
    return None


def default_output_device() -> AudioDevice | None:
    """Los parlantes o auriculares predeterminados del sistema."""
    try:
        index = sd.query_devices(kind="output")["index"]
    except (ValueError, sd.PortAudioError):
        return None
    return list_devices()[index]


def _copy_number(name: str) -> int:
    """Windows numera los dispositivos repetidos: si hay dos cables iguales,
    el segundo es "CABLE Input (2- VB-Audio Virtual Cable)". Con MME el nombre
    viene cortado a 31 letras, pero el número está al principio y se conserva."""
    match = re.search(r"\((\d+)-\s", name)
    return int(match.group(1)) if match else 1


def find_virtual_mic_input(output: AudioDevice) -> AudioDevice | None:
    """La punta de entrada del mismo cable virtual que `output` (ej. "CABLE
    Output" para "CABLE Input"), para comprobar que el audio llega."""
    mic_name = virtual_mic_name(output.name)
    if mic_name is None:
        return None
    matches = [
        dev
        for dev in list_devices()
        if dev.max_input_channels > 0 and dev.name.lower().startswith(mic_name.lower())
    ]
    same_cable = [dev for dev in matches if _copy_number(dev.name) == _copy_number(output.name)]
    matches = same_cable or matches
    same_hostapi = [dev for dev in matches if dev.hostapi == output.hostapi]
    return (same_hostapi or matches or [None])[0]


def _find_real_microphone(hostapi: int) -> AudioDevice | None:
    for dev in list_devices():
        lowered = dev.name.lower()
        if (
            dev.max_input_channels > 0
            and dev.hostapi == hostapi
            and not is_virtual_mic_input(dev.name)
            and not any(word in lowered for word in _NOT_A_MICROPHONE)
            and any(word in lowered for word in _MICROPHONE_WORDS)
        ):
            return dev
    return None


def resolve_input_device(requested: int | None) -> tuple[int | None, list[str]]:
    """Decide de qué micrófono escuchar. Devuelve el índice y una lista de
    avisos para mostrarle al usuario.

    Sin `--input-device` se usa el micrófono predeterminado del sistema,
    salvo que ese predeterminado sea un cable virtual: en ese caso se busca
    un micrófono real en su lugar."""
    if requested is not None:
        try:
            dev = sd.query_devices(requested, "input")
        except (ValueError, sd.PortAudioError):
            return requested, []  # el error claro lo da quien abra el stream
        if is_virtual_mic_input(dev["name"]):
            return requested, [
                f"Aviso: [{requested}] {dev['name']} es un micrófono virtual, no tu micrófono real: "
                "desde ahí clonavoz no va a escuchar tu voz (solo sirve si querés traducir el audio "
                "de la llamada). Para traducir tu voz, elegí tu micrófono real con --input-device "
                "(ver `clonavoz devices`)."
            ]
        return requested, []

    try:
        default = sd.query_devices(kind="input")
    except (ValueError, sd.PortAudioError):
        return None, []
    if not is_virtual_mic_input(default["name"]):
        return default["index"], []

    replacement = _find_real_microphone(default["hostapi"])
    if replacement is None:
        return default["index"], [
            f"Aviso: el micrófono predeterminado del sistema es '{default['name']}', que es un "
            "micrófono virtual (por donde sale la traducción), no tu voz. Elegí tu micrófono real "
            "con --input-device (ver `clonavoz devices`), o ponelo como predeterminado en la "
            "configuración de sonido del sistema."
        ]
    return replacement.index, [
        f"El micrófono predeterminado del sistema es '{default['name']}', que es el micrófono "
        f"virtual (por donde sale la traducción), no tu voz. Se usa en su lugar: "
        f"[{replacement.index}] {replacement.name}. Para elegir otro: --input-device "
        "(ver `clonavoz devices`)."
    ]


def print_devices() -> None:
    try:
        default_input = sd.query_devices(kind="input")["index"]
    except (ValueError, sd.PortAudioError):
        default_input = None
    try:
        default_output = sd.query_devices(kind="output")["index"]
    except (ValueError, sd.PortAudioError):
        default_output = None
    hostapis = sd.query_hostapis()

    for dev in list_devices():
        kinds = []
        if dev.max_input_channels > 0:
            kinds.append(f"entrada:{dev.max_input_channels}ch")
        if dev.max_output_channels > 0:
            kinds.append(f"salida:{dev.max_output_channels}ch")
        marks = []
        if dev.index == default_input:
            marks.append("entrada predeterminada")
        if dev.index == default_output:
            marks.append("salida predeterminada")
        if dev.max_input_channels > 0 and is_virtual_mic_input(dev.name):
            marks.append("micrófono virtual")
        line = f"[{dev.index:2d}] {dev.name}  ({hostapis[dev.hostapi]['name']}; {', '.join(kinds)})"
        if marks:
            line += "  <- " + ", ".join(marks)
        print(line)
