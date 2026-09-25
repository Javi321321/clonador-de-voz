"""Un `sounddevice` falso para las pruebas: simula dispositivos de audio (por
ejemplo, la lista de un Windows con VB-CABLE) sin PortAudio ni hardware.

Uso: `fake_sounddevice.install()` antes de importar módulos de clonavoz, y
`fake_sounddevice.configure(...)` en cada prueba.
"""
from __future__ import annotations

import sys

import numpy as np


class PortAudioError(Exception):
    pass


_devices: list[dict] = []
_hostapis: list[str] = []
_default_input: int | None = None
_default_output: int | None = None
streams: list = []  # streams abiertos, para inspeccionarlos en las pruebas


def install() -> None:
    sys.modules["sounddevice"] = sys.modules[__name__]


def device(name, hostapi=0, inputs=0, outputs=0, rate=44100, rates=None, channels=None) -> dict:
    """`rates`/`channels`: lo único que acepta el dispositivo al abrirlo
    (None = acepta cualquier cosa, como MME en Windows)."""
    return {
        "name": name,
        "hostapi": hostapi,
        "max_input_channels": inputs,
        "max_output_channels": outputs,
        "default_samplerate": float(rate),
        "rates": rates,
        "channels": channels,
    }


def configure(devices, hostapis=("MME",), default_input=None, default_output=None) -> None:
    global _devices, _hostapis, _default_input, _default_output
    _devices, _hostapis = list(devices), list(hostapis)
    _default_input, _default_output = default_input, default_output
    streams.clear()


def query_devices(device=None, kind=None):
    if device is None and kind is None:
        return [query_devices(i) for i in range(len(_devices))]
    if device is None:
        device = _default_input if kind == "input" else _default_output
        if device is None:
            raise PortAudioError(f"No default {kind} device")
    if not 0 <= device < len(_devices):
        raise ValueError(f"No such device: {device}")
    info = {k: v for k, v in _devices[device].items() if k not in ("rates", "channels")}
    info["index"] = device
    if kind and info[f"max_{kind}_channels"] < 1:
        raise ValueError(f"Not an {kind} device: {info['name']!r}")
    return info


def query_hostapis(index=None):
    apis = tuple({"name": name} for name in _hostapis)
    return apis if index is None else apis[index]


class _Stream:
    kind = ""

    def __init__(self, device=None, samplerate=None, channels=None, dtype=None, blocksize=None, callback=None):
        spec = _devices[device]
        if spec["rates"] is not None and int(samplerate) not in spec["rates"]:
            raise PortAudioError(f"Error opening {self.kind}: Invalid sample rate [PaErrorCode -9997]")
        if spec["channels"] is not None and channels not in spec["channels"]:
            raise PortAudioError(f"Error opening {self.kind}: Invalid number of channels [PaErrorCode -9998]")
        self.device, self.samplerate, self.channels = device, samplerate, channels
        self.blocksize, self.callback = blocksize, callback
        self.started = self.closed = False
        self.written: list[np.ndarray] = []
        streams.append(self)

    def start(self):
        self.started = True

    def stop(self):
        self.started = False

    def close(self):
        self.closed = True


class InputStream(_Stream):
    kind = "InputStream"

    def feed(self, mono_block: np.ndarray) -> None:
        """Simula que el dispositivo entrega un bloque (repetido en cada canal)."""
        indata = np.repeat(np.asarray(mono_block, dtype=np.float32).reshape(-1, 1), self.channels, axis=1)
        self.callback(indata, len(indata), None, None)


class OutputStream(_Stream):
    kind = "OutputStream"

    def write(self, frames: np.ndarray) -> bool:
        self.written.append(np.array(frames))
        return False


def windows_with_vb_cable(default_input: int = 2) -> None:
    """Lista típica de un Windows en español con VB-CABLE instalado: cada
    dispositivo aparece una vez por API. Los de WASAPI solo abren a 48 kHz."""
    d = device
    configure(
        [
            d("Asignador de sonido Microsoft - Input", 0, inputs=2),  # 0
            d("CABLE Output (VB-Audio Virtual ", 0, inputs=8),  # 1
            d("Micrófono (Realtek(R) Audio)", 0, inputs=2),  # 2
            d("Asignador de sonido Microsoft - Output", 0, outputs=2),  # 3
            d("CABLE Input (VB-Audio Virtual C", 0, outputs=8),  # 4
            d("Altavoces (Realtek(R) Audio)", 0, outputs=2),  # 5
            d("Controlador primario de captura de sonido", 1, inputs=2),  # 6
            d("CABLE Output (VB-Audio Virtual Cable)", 1, inputs=8),  # 7
            d("Micrófono (Realtek(R) Audio)", 1, inputs=2),  # 8
            d("CABLE Input (VB-Audio Virtual Cable)", 2, outputs=2, rate=48000, rates=[48000]),  # 9
            d("CABLE Output (VB-Audio Virtual Cable)", 2, inputs=2, rate=48000, rates=[48000]),  # 10
            d("Micrófono (Realtek(R) Audio)", 2, inputs=2, rate=48000, rates=[48000]),  # 11
        ],
        hostapis=("MME", "Windows DirectSound", "Windows WASAPI"),
        default_input=default_input,
        default_output=5,
    )
