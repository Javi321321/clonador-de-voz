"""Salida de consola en vivo: el medidor de nivel del micrófono y una línea
de estado que se redibuja en el lugar sin mezclarse con los mensajes
normales (transcripciones, avisos). Si la salida no es una terminal (por
ejemplo, redirigida a un archivo), la línea de estado no se dibuja.

Solo usa caracteres ASCII y "\\r" (nada de códigos ANSI), para que se vea
bien también en la consola clásica de Windows.
"""
from __future__ import annotations

import shutil
import sys
import threading

METER_FLOOR_DB = -60.0


def format_meter(level_db: float, width: int = 20) -> str:
    clamped = min(max(level_db, METER_FLOOR_DB), 0.0)
    filled = round((clamped - METER_FLOOR_DB) / -METER_FLOOR_DB * width)
    return f"[{'#' * filled}{'-' * (width - filled)}] {clamped:4.0f} dB"


class StatusLine:
    def __init__(self, stream=None) -> None:
        self._stream = stream or sys.stdout
        isatty = getattr(self._stream, "isatty", None)
        self.enabled = bool(isatty and isatty())
        self._lock = threading.Lock()
        self._text = ""

    def update(self, text: str) -> None:
        if not self.enabled:
            return
        text = text[: shutil.get_terminal_size((80, 20)).columns - 1]
        with self._lock:
            self._stream.write("\r" + text.ljust(len(self._text)))
            self._stream.flush()
            self._text = text

    def print(self, message: str) -> None:
        """Imprime un mensaje normal por encima de la línea de estado."""
        with self._lock:
            if self._text:
                self._stream.write("\r" + " " * len(self._text) + "\r")
            self._stream.write(message + "\n")
            if self._text:
                self._stream.write(self._text)
            self._stream.flush()

    def clear(self) -> None:
        with self._lock:
            if self._text:
                self._stream.write("\r" + " " * len(self._text) + "\r")
                self._stream.flush()
                self._text = ""
