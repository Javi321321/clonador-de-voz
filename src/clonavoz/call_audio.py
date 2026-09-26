"""El audio de la llamada: lo que dicen los demás, para traducírtelo.

En Windows se graba lo que suena en la computadora (el "loopback" de WASAPI),
sin instalar nada: la voz de la otra persona sale de Zoom, Meet, Teams,
Discord, WhatsApp o del navegador por tus auriculares, y clonavoz la escucha
de ahí.

Desde Windows 10 versión 2004 (2020) se puede grabar todo lo que suena *menos*
lo que reproduce clonavoz, y eso es lo que se usa: la traducción de lo que te
dicen también suena en tus auriculares, y si clonavoz la volviera a escuchar
la traduciría otra vez, en un bucle. En Windows más viejos se graba todo lo
que suena en la salida predeterminada y, mientras suena la traducción, no se
escucha (ver `excludes_own_audio` y `IncomingPipeline`).

En Linux y macOS no hay algo equivalente sin instalar nada: hay que elegir a
mano un dispositivo de entrada que tenga el audio de la llamada (en Linux, el
"Monitor of ..." de tu salida; en macOS, BlackHole con un dispositivo de
salida múltiple), con --call-device.

Todo se entrega igual que el micrófono (ver audio_io.MicrophoneStream):
bloques mono de `frame_size` muestras a 16 kHz, con el nivel y estadísticas.
"""
from __future__ import annotations

import ctypes
import os
import sys
import threading
import time
import uuid
from ctypes import POINTER, Structure, byref, c_int, c_int64, c_long, c_ubyte, c_uint32, c_uint64, c_ushort, c_void_p

import numpy as np

from .audio_devices import SAMPLE_RATE
from .audio_io import AudioDeviceError, MicStats, StreamResampler, to_dbfs

# Si Windows no manda nada durante este tiempo (nadie suena), se entrega
# silencio: el detector de voz lo necesita para saber que la frase terminó.
_GAP_SECONDS = 0.1
# Windows puede llamar al objeto que avisa que la grabación está lista desde
# otro hilo, incluso un rato después: se guardan acá para que nunca se borren.
_HANDLERS: list = []


class _Guid(Structure):
    _fields_ = [("Data1", c_uint32), ("Data2", c_ushort), ("Data3", c_ushort), ("Data4", c_ubyte * 8)]


def _guid(text: str) -> _Guid:
    return _Guid.from_buffer_copy(uuid.UUID(text).bytes_le)


class _WaveFormat(Structure):  # WAVEFORMATEX
    _pack_ = 1
    _fields_ = [
        ("wFormatTag", c_ushort), ("nChannels", c_ushort), ("nSamplesPerSec", c_uint32),
        ("nAvgBytesPerSec", c_uint32), ("nBlockAlign", c_ushort), ("wBitsPerSample", c_ushort), ("cbSize", c_ushort),
    ]


class _WaveFormatExtensible(Structure):  # WAVEFORMATEXTENSIBLE
    _pack_ = 1
    _fields_ = [("Format", _WaveFormat), ("wValidBitsPerSample", c_ushort), ("dwChannelMask", c_uint32), ("SubFormat", _Guid)]


class _Blob(Structure):
    _fields_ = [("cbSize", c_uint32), ("pBlobData", c_void_p)]


class _PropVariant(Structure):  # PROPVARIANT con un BLOB
    _fields_ = [("vt", c_ushort), ("r1", c_ushort), ("r2", c_ushort), ("r3", c_ushort), ("blob", _Blob)]


class _ProcessLoopbackParams(Structure):  # AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS
    _fields_ = [("TargetProcessId", c_uint32), ("ProcessLoopbackMode", c_int)]


class _ActivationParams(Structure):  # AUDIOCLIENT_ACTIVATION_PARAMS
    _fields_ = [("ActivationType", c_int), ("ProcessLoopbackParams", _ProcessLoopbackParams)]


_IID_UNKNOWN = "00000000-0000-0000-C000-000000000046"
_IID_AGILE = "94EA2B94-E9CC-49E0-C0FF-EE64CA8F5B90"
_IID_COMPLETION_HANDLER = "41D949AB-9862-444A-80F6-C261334DA5EB"
_IID_AUDIO_CLIENT = "1CB9AD4C-DBFA-4C32-B178-C2F568A703B2"
_IID_CAPTURE_CLIENT = "C8ADBD64-E71E-48A0-A4DE-185C395CD317"
_CLSID_DEVICE_ENUMERATOR = "BCDE0395-E52F-467C-8E3D-C4579291692E"
_IID_DEVICE_ENUMERATOR = "A95664D2-9614-4F35-A746-DE8DB63617E6"
_SUBTYPE_FLOAT = uuid.UUID("00000003-0000-0010-8000-00AA00389B71").bytes_le
_PROCESS_LOOPBACK_DEVICE = "VAD\\Process_Loopback"
_CLSCTX_ALL = 0x17
_VT_BLOB = 65
_LOOPBACK = 0x00020000
_EVENT_CALLBACK = 0x00040000
_AUTOCONVERT_PCM = 0x80000000
_SRC_DEFAULT_QUALITY = 0x08000000
_BUFFER_SILENT = 0x2
_E_NOINTERFACE = -2147467262  # 0x80004002
_WAVE_FORMAT_PCM, _WAVE_FORMAT_FLOAT, _WAVE_FORMAT_EXTENSIBLE = 1, 3, 0xFFFE


def _pcm_format(rate: int, channels: int, float32: bool) -> _WaveFormat:
    bits = 32 if float32 else 16
    align = channels * bits // 8
    return _WaveFormat(_WAVE_FORMAT_FLOAT if float32 else _WAVE_FORMAT_PCM, channels, rate, rate * align, align, bits, 0)


def _sample_type(fmt: _WaveFormat, address: int) -> np.dtype:
    """Tipo de cada muestra (float32, int16 o int32) de un formato de Windows."""
    tag = fmt.wFormatTag
    if tag == _WAVE_FORMAT_EXTENSIBLE:
        extensible = _WaveFormatExtensible.from_address(address)
        tag = _WAVE_FORMAT_FLOAT if bytes(extensible.SubFormat) == _SUBTYPE_FLOAT else _WAVE_FORMAT_PCM
    if tag == _WAVE_FORMAT_FLOAT and fmt.wBitsPerSample == 32:
        return np.dtype(np.float32)
    if tag == _WAVE_FORMAT_PCM and fmt.wBitsPerSample in (16, 32):
        return np.dtype(np.int16 if fmt.wBitsPerSample == 16 else np.int32)
    raise AudioDeviceError(f"Formato de audio de Windows no soportado ({tag}, {fmt.wBitsPerSample} bits).")


class FrameAssembler:
    """Convierte lo que llega de Windows (intercalado, a su frecuencia y en su
    tipo) en bloques mono de `frame_size` muestras a 16 kHz."""

    def __init__(self, rate: int, channels: int, dtype, frame_size: int, on_frame, stats: MicStats) -> None:
        self.rate, self.channels, self.dtype = rate, channels, np.dtype(dtype)
        self._scale = 1.0 if self.dtype.kind == "f" else float(np.iinfo(self.dtype).max)
        self._resampler = StreamResampler(rate, SAMPLE_RATE) if rate != SAMPLE_RATE else None
        self._frame_size = frame_size
        self._on_frame = on_frame
        self._pending = np.zeros(0, dtype=np.float32)
        self.stats = stats
        self.level = 0.0

    def push_bytes(self, raw: bytes) -> None:
        samples = np.frombuffer(raw, dtype=self.dtype).astype(np.float32) / self._scale
        self.push(samples.reshape(-1, self.channels).mean(axis=1) if self.channels > 1 else samples)

    def push_silence(self, frames: int) -> None:
        self.push(np.zeros(frames, dtype=np.float32))

    def push(self, block: np.ndarray) -> None:
        peak = float(np.max(np.abs(block))) if len(block) else 0.0
        stats = self.stats
        stats.blocks += 1
        stats.last_block_time = time.monotonic()
        if peak > 0.0:
            stats.nonzero_blocks += 1
        stats.max_peak = max(stats.max_peak, peak)
        self.level = max(self.level, peak)
        if self._resampler is not None:
            block = self._resampler.process(block)
        self._pending = np.concatenate((self._pending, block))
        size = self._frame_size
        while len(self._pending) >= size:
            self._on_frame(self._pending[:size].copy())
            self._pending = self._pending[size:]


class _Com:
    """Llamadas a interfaces COM de Windows con ctypes, sin dependencias extra."""

    def __init__(self) -> None:
        self.ole32 = ctypes.OleDLL("ole32")
        self.kernel32 = ctypes.WinDLL("kernel32")
        self.kernel32.CreateEventW.restype = c_void_p
        self.kernel32.CreateEventW.argtypes = [c_void_p, c_int, c_int, c_void_p]
        self.kernel32.WaitForSingleObject.argtypes = [c_void_p, c_uint32]
        self.kernel32.CloseHandle.argtypes = [c_void_p]
        self.ole32.CoTaskMemFree.argtypes = [c_void_p]
        self.ole32.CoTaskMemFree.restype = None

    @staticmethod
    def method(obj: c_void_p, index: int, *argtypes):
        """El método número `index` de la interfaz `obj` (devuelve HRESULT: si
        falla, ctypes lanza OSError con el código)."""
        function = ctypes.cast(obj, POINTER(POINTER(c_void_p)))[0][index]
        bound = ctypes.WINFUNCTYPE(ctypes.HRESULT, c_void_p, *argtypes)(function)
        return lambda *args: bound(obj, *args)

    @staticmethod
    def release(obj: c_void_p) -> None:
        if obj:
            function = ctypes.cast(obj, POINTER(POINTER(c_void_p)))[0][2]
            ctypes.WINFUNCTYPE(ctypes.c_ulong, c_void_p)(function)(obj)


class _CompletionHandler:
    """Objeto COM mínimo (IActivateAudioInterfaceCompletionHandler, ágil) que
    avisa cuando Windows terminó de preparar la grabación."""

    def __init__(self) -> None:
        query = ctypes.WINFUNCTYPE(c_long, c_void_p, POINTER(_Guid), POINTER(c_void_p))
        counter = ctypes.WINFUNCTYPE(ctypes.c_ulong, c_void_p)
        completed = ctypes.WINFUNCTYPE(c_long, c_void_p, c_void_p)

        class Vtbl(Structure):
            _fields_ = [("QueryInterface", query), ("AddRef", counter), ("Release", counter), ("ActivateCompleted", completed)]

        class Obj(Structure):
            _fields_ = [("lpVtbl", POINTER(Vtbl))]

        self.done = threading.Event()
        self._interfaces = {bytes(_guid(iid)) for iid in (_IID_UNKNOWN, _IID_AGILE, _IID_COMPLETION_HANDLER)}
        # Todo queda guardado en el objeto: Windows lo llama desde otro hilo.
        self._callbacks = (query(self._query), counter(self._count), counter(self._count), completed(self._completed))
        self._vtbl = Vtbl(*self._callbacks)
        self._obj = Obj(ctypes.pointer(self._vtbl))
        self.pointer = ctypes.addressof(self._obj)

    @staticmethod
    def _count(this) -> int:
        return 1  # vive siempre (ver _HANDLERS): no hace falta contar referencias

    def _query(self, this, iid, out) -> int:
        if bytes(iid.contents) in self._interfaces:
            out[0] = this
            return 0
        out[0] = None
        return _E_NOINTERFACE

    def _completed(self, this, operation) -> int:
        self.done.set()
        return 0


class _WasapiLoopback:
    """La grabación de WASAPI en sí; todo en el hilo que la usa (COM MTA)."""

    def __init__(self, exclude_own_process: bool) -> None:
        self.com = _Com()
        self.exclude_own_process = exclude_own_process
        self._client = c_void_p()
        self._capture = c_void_p()
        self._event = None
        self.rate = self.channels = 0
        self.dtype = np.dtype(np.float32)
        self.block_align = 0
        # Para diagnosticar: paquetes que mandó Windows y cuántos venían marcados como silencio.
        self.packets = self.silent_packets = 0

    def open(self) -> None:
        try:
            self.com.ole32.CoInitializeEx(None, 0)  # COINIT_MULTITHREADED
        except OSError:
            pass  # ya estaba inicializado en este hilo
        if self.exclude_own_process:
            self._open_process_loopback()
        else:
            self._open_endpoint_loopback()
        get_service = _Com.method(self._client, 14, POINTER(_Guid), POINTER(c_void_p))
        get_service(byref(_guid(_IID_CAPTURE_CLIENT)), byref(self._capture))
        _Com.method(self._client, 10)()  # Start

    def _activate_process_loopback(self) -> c_void_p:
        """IAudioClient que graba todo lo que suena menos este proceso (y sus hijos)."""
        activate = ctypes.WinDLL("Mmdevapi").ActivateAudioInterfaceAsync
        activate.restype = ctypes.HRESULT
        activate.argtypes = [ctypes.c_wchar_p, POINTER(_Guid), POINTER(_PropVariant), c_void_p, POINTER(c_void_p)]
        params = _ActivationParams(1, _ProcessLoopbackParams(os.getpid(), 1))  # PROCESS_LOOPBACK, EXCLUDE
        variant = _PropVariant(vt=_VT_BLOB)
        variant.blob.cbSize = ctypes.sizeof(params)
        variant.blob.pBlobData = ctypes.addressof(params)
        handler = _CompletionHandler()
        _HANDLERS.append(handler)
        operation = c_void_p()
        activate(_PROCESS_LOOPBACK_DEVICE, byref(_guid(_IID_AUDIO_CLIENT)), byref(variant), handler.pointer, byref(operation))
        try:
            if not handler.done.wait(10):
                raise OSError("Windows no terminó de preparar la grabación")
            result, client = c_long(), c_void_p()
            _Com.method(operation, 3, POINTER(c_long), POINTER(c_void_p))(byref(result), byref(client))
        finally:
            _Com.release(operation)
        if result.value < 0:
            raise OSError(None, "no se pudo activar la grabación", None, result.value)
        return client

    def _open_process_loopback(self) -> None:
        # Windows convierte a cualquier formato: primero directo a 16 kHz mono.
        errors = []
        for rate, channels, float32 in ((SAMPLE_RATE, 1, True), (48000, 2, False), (44100, 2, False)):
            client = self._activate_process_loopback()
            fmt = _pcm_format(rate, channels, float32)
            initialize = _Com.method(client, 3, c_int, c_uint32, c_int64, c_int64, POINTER(_WaveFormat), c_void_p)
            flags = _LOOPBACK | _EVENT_CALLBACK | _AUTOCONVERT_PCM | _SRC_DEFAULT_QUALITY
            try:
                initialize(0, flags, 1_000_000, 0, byref(fmt), None)  # compartido, 100 ms de buffer
            except OSError as exc:
                _Com.release(client)
                errors.append(f"{rate} Hz: {exc}")
                continue
            self._client = client
            self.rate, self.channels = rate, channels
            self.dtype = np.dtype(np.float32 if float32 else np.int16)
            self.block_align = fmt.nBlockAlign
            self._event = self.com.kernel32.CreateEventW(None, 0, 0, None)
            _Com.method(client, 13, c_void_p)(self._event)  # SetEventHandle
            return
        raise OSError("; ".join(errors))

    def _open_endpoint_loopback(self) -> None:
        enumerator, device = c_void_p(), c_void_p()
        self.com.ole32.CoCreateInstance(
            byref(_guid(_CLSID_DEVICE_ENUMERATOR)), None, _CLSCTX_ALL, byref(_guid(_IID_DEVICE_ENUMERATOR)), byref(enumerator)
        )
        try:
            # GetDefaultAudioEndpoint(eRender, eConsole): la salida predeterminada.
            _Com.method(enumerator, 4, c_int, c_int, POINTER(c_void_p))(0, 0, byref(device))
            try:
                _Com.method(device, 3, POINTER(_Guid), c_uint32, c_void_p, POINTER(c_void_p))(
                    byref(_guid(_IID_AUDIO_CLIENT)), _CLSCTX_ALL, None, byref(self._client)
                )
            finally:
                _Com.release(device)
        finally:
            _Com.release(enumerator)
        mix = c_void_p()
        _Com.method(self._client, 8, POINTER(c_void_p))(byref(mix))  # GetMixFormat
        try:
            fmt = _WaveFormat.from_address(mix.value)
            self.rate, self.channels, self.block_align = fmt.nSamplesPerSec, fmt.nChannels, fmt.nBlockAlign
            self.dtype = _sample_type(fmt, mix.value)
            initialize = _Com.method(self._client, 3, c_int, c_uint32, c_int64, c_int64, c_void_p, c_void_p)
            initialize(0, _LOOPBACK, 1_000_000, 0, mix, None)
        finally:
            self.com.ole32.CoTaskMemFree(mix)

    def read(self, assembler: FrameAssembler, stop: threading.Event) -> None:
        """Entrega lo grabado hasta que `stop` se active."""
        next_packet = _Com.method(self._capture, 5, POINTER(c_uint32))
        get_buffer = _Com.method(
            self._capture, 3, POINTER(c_void_p), POINTER(c_uint32), POINTER(c_uint32), POINTER(c_uint64), POINTER(c_uint64)
        )
        release_buffer = _Com.method(self._capture, 4, c_uint32)
        size, data, frames, flags, position, qpc = c_uint32(), c_void_p(), c_uint32(), c_uint32(), c_uint64(), c_uint64()
        delivered = time.monotonic()
        while not stop.is_set():
            if self._event is not None:
                self.com.kernel32.WaitForSingleObject(self._event, 20)
            else:
                time.sleep(0.01)
            got = False
            while True:
                next_packet(byref(size))
                if size.value == 0:
                    break
                get_buffer(byref(data), byref(frames), byref(flags), byref(position), byref(qpc))
                count = frames.value
                self.packets += 1
                if flags.value & _BUFFER_SILENT or not data.value:
                    self.silent_packets += 1
                    raw = bytes(count * self.block_align)
                else:
                    raw = ctypes.string_at(data.value, count * self.block_align)
                release_buffer(count)
                assembler.push_bytes(raw)
                got = True
            now = time.monotonic()
            if got:
                delivered = now
            elif now - delivered > _GAP_SECONDS:
                assembler.push_silence(int((now - delivered) * assembler.rate))
                delivered = now

    def close(self) -> None:
        if self._client:
            try:
                _Com.method(self._client, 11)()  # Stop
            except OSError:
                pass
        _Com.release(self._capture)
        _Com.release(self._client)
        self._capture = self._client = c_void_p()
        if self._event is not None:
            self.com.kernel32.CloseHandle(self._event)
            self._event = None


def process_loopback_supported() -> bool:
    """Windows 10 versión 2004 (compilación 19041) o posterior."""
    if sys.platform != "win32":
        return False
    return sys.getwindowsversion().build >= 19041


class CallAudioStream:
    """Lo que suena en la computadora (la llamada), como un micrófono más.

    `exclude_own_audio`: grabar todo menos lo que reproduce clonavoz (si
    Windows lo permite; si no, `excludes_own_audio` queda en False y hay que
    dejar de escuchar mientras suena la traducción)."""

    def __init__(self, on_frame=None, frame_size: int = 512, exclude_own_audio: bool = True) -> None:
        self._on_frame = on_frame
        self._frame_size = frame_size
        self._want_exclusion = exclude_own_audio
        self.excludes_own_audio = False
        self.stats = MicStats()
        self.description = ""
        self._assembler: FrameAssembler | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._error: BaseException | None = None
        self._ready = threading.Event()
        self._wasapi: _WasapiLoopback | None = None

    @property
    def diagnostics(self) -> str:
        """Qué llegó de Windows (para encontrar problemas)."""
        w = self._wasapi
        if w is None:
            return "sin abrir"
        return (
            f"{w.rate} Hz, {w.channels} canal(es), {w.dtype.name}; {w.packets} paquetes "
            f"({w.silent_packets} marcados como silencio)"
        )

    def start(self) -> None:
        if sys.platform != "win32":
            raise AudioDeviceError(
                "Escuchar la llamada sin elegir un dispositivo solo funciona en Windows. Elegí el "
                "dispositivo que tiene el audio de la llamada con --call-device (en Linux, el \"Monitor "
                "of ...\" de tu salida; ver `clonavoz devices`)."
            )
        attempts = [True, False] if self._want_exclusion and process_loopback_supported() else [False]
        errors = []
        for exclude in attempts:
            self._stop.clear()
            self._ready.clear()
            self._error = None
            self._thread = threading.Thread(target=self._run, args=(exclude,), daemon=True, name="audio-llamada")
            self._thread.start()
            self._ready.wait(15)
            if self._error is None and self._ready.is_set():
                self.excludes_own_audio = exclude
                return
            self._stop.set()
            self._thread.join(timeout=2)
            errors.append(f"{'sin clonavoz' if exclude else 'todo'}: {self._error or 'no respondió'}")
        raise AudioDeviceError(
            "No se pudo escuchar el audio de la computadora (la llamada):\n  " + "\n  ".join(errors)
            + "\nRevisá que haya una salida de audio (auriculares o parlantes) activa en Windows."
        )

    def _run(self, exclude: bool) -> None:
        wasapi = self._wasapi = _WasapiLoopback(exclude)
        try:
            wasapi.open()
            self._assembler = FrameAssembler(
                wasapi.rate, wasapi.channels, wasapi.dtype, self._frame_size, self._deliver, self.stats
            )
            self.description = _default_output_name() + (
                " (todo lo que suena, menos clonavoz)" if exclude else " (todo lo que suena)"
            )
            self._ready.set()
            wasapi.read(self._assembler, self._stop)
        except Exception as exc:  # noqa: BLE001 - se informa a quien abrió (o después, en `error`)
            self._error = exc
            self._ready.set()
        finally:
            wasapi.close()

    def _deliver(self, frame: np.ndarray) -> None:
        if self._on_frame is not None:
            self._on_frame(frame)

    @property
    def error(self) -> BaseException | None:
        """Si la grabación se cortó por un error después de empezar."""
        return self._error

    def pop_level_db(self) -> float:
        if self._assembler is None:
            return to_dbfs(0.0)
        level, self._assembler.level = self._assembler.level, 0.0
        return to_dbfs(level)

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)


def _default_output_name() -> str:
    try:
        import sounddevice as sd

        return sd.query_devices(kind="output")["name"]
    except Exception:  # noqa: BLE001 - solo es para mostrarlo
        return "la salida de audio predeterminada"
