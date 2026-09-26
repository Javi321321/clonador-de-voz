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
# preferencia: si hay VB-CABLE y además Voicemeeter, se prefiere el cable. La
# entrada de VB-CABLE a veces aparece con el nombre genérico de Windows,
# "Speakers (VB-Audio Virtual Cable)" o "Altavoces (...)", en vez de "CABLE Input".
_VIRTUAL_DEVICE_HINTS = {
    "Windows": ["cable input", "vb-audio virtual", "cable in ", "vb-audio", "voicemeeter"],
    "Darwin": ["blackhole"],
    "Linux": ["clonavoz", "null sink"],
}

# Punta de entrada de los cables virtuales (lo que usa la app de videollamada
# como micrófono). "CABLE Output" de VB-CABLE se detecta aparte (ver
# `is_virtual_mic_input`) porque también existen "CABLE-A Output", etc.
_VIRTUAL_INPUT_HINTS = ("blackhole", "soundflower", "clonavoz", "monitor of", "vb-audio")

# Dispositivos que no son uno de verdad sino "el predeterminado de Windows": el
# "mapeador" de MME y el controlador primario de DirectSound (su nombre cambia
# según el idioma de Windows). Si el predeterminado es el cable virtual, apuntan
# al cable.
_DEFAULT_ALIASES = ("mapp", "mapea", "asignador", "primary", "primari")
# Entradas que no son un micrófono real: esas y las mezclas estéreo.
_NOT_A_MICROPHONE = (*_DEFAULT_ALIASES, "stereo mix", "mezcla est", "what u hear", "loopback", "monitor")
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
    if "vb-audio virtual" in output_device_name.lower():
        return "CABLE Output"  # "Speakers (VB-Audio Virtual Cable)" es la entrada de VB-CABLE
    if "blackhole" in lowered:
        return base
    if "clonavoz" in lowered:
        return "Monitor of ClonaVoz_Mic"
    return None


def is_default_alias(name: str) -> bool:
    """True si `name` no es un dispositivo sino el predeterminado de Windows
    (ej. "Microsoft Sound Mapper - Output")."""
    lowered = name.lower()
    return any(word in lowered for word in _DEFAULT_ALIASES)


# Para elegir dónde escuchás: mejor auriculares, si no parlantes, si no cualquier
# salida (ej. el HDMI de un monitor, que quizás no tiene parlantes).
_HEADPHONE_WORDS = ("auricular", "headphone", "headset", "casque", "kopfh", "fone")
_SPEAKER_WORDS = ("altavoc", "speaker", "parlante", "alto-falante", "haut-parleur", "lautsprecher")


def best_listening_device(devices: list[AudioDevice]) -> AudioDevice | None:
    """De estas salidas, la mejor para escuchar vos: auriculares, parlantes u otra."""
    for words in (_HEADPHONE_WORDS, _SPEAKER_WORDS):
        for dev in devices:
            if any(word in dev.name.lower() for word in words):
                return dev
    return devices[0] if devices else None


def is_virtual_output(name: str) -> bool:
    """True si `name` es la punta de salida de un cable virtual (lo que después
    graba la videollamada), y no parlantes o auriculares de verdad."""
    lowered = name.lower()
    return any(hint in lowered for hint in _VIRTUAL_DEVICE_HINTS.get(platform.system(), []))


def find_virtual_output_device() -> AudioDevice | None:
    """Busca por nombre un dispositivo de salida que sea un micrófono
    virtual ya instalado, para usarlo como salida del audio traducido."""
    hints = _VIRTUAL_DEVICE_HINTS.get(platform.system(), [])
    # La variante de 16 canales de VB-CABLE ("CABLE In 16 Ch") solo si no hay otra.
    outputs = sorted(
        (dev for dev in list_devices() if dev.max_output_channels > 0),
        key=lambda dev: "16 ch" in dev.name.lower(),
    )
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
    wanted = mic_name.lower()
    matches = [
        dev
        for dev in list_devices()
        if dev.max_input_channels > 0
        and (
            dev.name.lower().startswith(wanted)
            # por si Windows también le dio un nombre genérico ("Microphone (VB-Audio ...)")
            or (wanted == "cable output" and "vb-audio virtual" in dev.name.lower())
        )
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


def _same_name(device_name: str, wanted: str) -> bool:
    """Si `device_name` es el dispositivo llamado `wanted`. Con MME, Windows
    corta los nombres a 31 letras ("Micrófono (Intel® Smart Sound Te"): vale
    también si el nombre cortado es el principio del completo."""
    name = device_name.strip().casefold()
    return name == wanted or (len(name) >= 28 and wanted.startswith(name))


def find_input_by_name(name: str) -> AudioDevice | None:
    """El micrófono llamado `name`, como lo muestra Windows (ej. "Micrófono
    (Realtek(R) Audio)"), o que tiene `name` en su nombre (ej. "realtek").
    Cada dispositivo aparece una vez por API de audio: se prefiere la del
    micrófono predeterminado (MME en Windows), como cuando no se elige ninguno."""
    wanted = name.strip().casefold()
    if not wanted:
        return None
    inputs = [dev for dev in list_devices() if dev.max_input_channels > 0]
    try:
        default_api = sd.query_devices(kind="input")["hostapi"]
    except (ValueError, sd.PortAudioError):
        default_api = 0
    for matches in (
        [dev for dev in inputs if _same_name(dev.name, wanted)],
        [dev for dev in inputs if wanted in dev.name.casefold()],
    ):
        if matches:
            return ([dev for dev in matches if dev.hostapi == default_api] or matches)[0]
    return None


def resolve_input_device(requested: int | str | None) -> tuple[int | None, list[str]]:
    """Decide de qué micrófono escuchar. Devuelve el índice y una lista de
    avisos para mostrarle al usuario.

    `requested` es el número del dispositivo (ver `clonavoz devices`) o su
    nombre. Sin `--input-device` se usa el micrófono predeterminado del
    sistema, salvo que ese predeterminado sea un cable virtual: en ese caso
    se busca un micrófono real en su lugar."""
    if isinstance(requested, str):
        found = find_input_by_name(requested)
        if found is None:
            index, notes = resolve_input_device(None)
            return index, [
                f"Aviso: no se encontró el micrófono '{requested}' (ver `clonavoz devices`): se usa el "
                "predeterminado.",
                *notes,
            ]
        requested = found.index
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
