"""Hablar más rápido sin cambiar el tono de la voz, para que la traducción
alcance a quien habla cuando se atrasa (como hace un intérprete).

- `SpeedUp`: WSOLA (superposición de pedazos elegidos por parecido), el
  método clásico para acelerar voz sin que suene "de ardilla". Hasta ~1.3x
  casi no se nota. Funciona de a pedazos: acelera la voz a medida que se
  genera, sin esperar la frase entera, y la velocidad puede cambiar en el
  camino (vuelve a la normal apenas la traducción te alcanza).
- `speed_up`: lo mismo, para una frase entera.
- `shorten_pauses`: acorta los silencios largos dentro de una frase.
"""
from __future__ import annotations

import numpy as np


class SpeedUp:
    """`push(audio)` devuelve lo que ya está listo; `finish()`, el resto.
    `speed` (> 1 es más rápido) se puede cambiar entre un `push` y otro."""

    def __init__(self, sample_rate: int, speed: float = 1.0) -> None:
        self.speed = speed
        self._frame = int(0.030 * sample_rate)  # ventanas de 30 ms
        self._hop = self._frame // 2
        self._search = int(0.012 * sample_rate)
        self._window = np.hanning(self._frame).astype(np.float32)
        self._input = np.zeros(0, dtype=np.float32)  # lo recibido que todavía hace falta
        self._offset = 0  # posición de `_input[0]` en todo lo recibido
        self._out = np.zeros(self._frame, dtype=np.float32)  # ventanas superpuestas, sin terminar
        self._norm = np.zeros(self._frame, dtype=np.float32)
        self._ideal = 0.0  # dónde empezaría la próxima ventana, a la velocidad pedida
        self._previous: int | None = None  # dónde empezó la ventana anterior

    def push(self, audio: np.ndarray) -> np.ndarray:
        self._input = np.concatenate([self._input, np.asarray(audio, dtype=np.float32).reshape(-1)])
        ready = []
        while self._needed() <= self._offset + len(self._input):
            ready.append(self._add_window())
        if self._previous is not None:
            keep = min(self._previous + self._hop, int(self._ideal) - self._search)
            if keep > self._offset:
                self._input = self._input[keep - self._offset :]
                self._offset = keep
        return np.concatenate(ready) if ready else np.zeros(0, dtype=np.float32)

    def finish(self) -> np.ndarray:
        """El final: lo que sigue a la última ventana, tal cual."""
        if self._previous is None:
            return self._input
        return self._slice(self._previous + self._hop, None)

    def _needed(self) -> int:
        """Hasta dónde hay que haber recibido para poner la próxima ventana."""
        needed = int(self._ideal) + self._search + self._frame
        if self._previous is not None:
            needed = max(needed, self._previous + self._hop + self._frame)
        return needed

    def _slice(self, start: int, length: int | None) -> np.ndarray:
        begin = start - self._offset
        return self._input[begin:] if length is None else self._input[begin : begin + length]

    def _add_window(self) -> np.ndarray:
        ideal = int(self._ideal)
        if self._previous is None:
            start = ideal
        else:
            # Lo que "seguiría naturalmente" a la ventana anterior, y se busca
            # cerca de la posición ideal el pedazo que mejor empalma con eso.
            natural = self._slice(self._previous + self._hop, self._frame)
            low = max(self._offset, ideal - self._search)
            region = self._slice(low, ideal + self._search + self._frame - low)
            start = low + int(np.argmax(np.correlate(region, natural, mode="valid")))
        segment = self._slice(start, self._frame)
        self._out += segment * self._window
        self._norm += self._window
        if self._previous is None:
            ready = segment[: self._hop].copy()  # no se superpone con nada: sale tal cual
        else:
            norm = self._norm[: self._hop].copy()
            norm[norm < 1e-3] = 1.0
            ready = self._out[: self._hop] / norm
        # Lo que ya salió no lo toca ninguna ventana más: se corre todo.
        self._out = np.concatenate([self._out[self._hop :], np.zeros(self._hop, dtype=np.float32)])
        self._norm = np.concatenate([self._norm[self._hop :], np.zeros(self._hop, dtype=np.float32)])
        self._previous = start
        self._ideal += self._hop * self.speed
        return ready


def speed_up(audio: np.ndarray, sample_rate: int, speed: float) -> np.ndarray:
    """`audio` más rápido (`speed` > 1), con el mismo tono."""
    audio = np.asarray(audio, dtype=np.float32)
    if speed <= 1.001 or len(audio) < 4 * int(0.030 * sample_rate):
        return audio
    stretch = SpeedUp(sample_rate, speed)
    return np.concatenate([stretch.push(audio), stretch.finish()])


def shorten_pauses(
    audio: np.ndarray, sample_rate: int, longer_than: float = 0.25, keep: float = 0.12, threshold_db: float = -40
) -> np.ndarray:
    """Acorta a `keep` segundos los silencios de más de `longer_than`."""
    audio = np.asarray(audio, dtype=np.float32)
    hop = sample_rate // 100  # 10 ms
    n = len(audio) // hop
    if n == 0:
        return audio
    loud = np.abs(audio[: n * hop]).reshape(n, hop).max(axis=1) > 10 ** (threshold_db / 20)
    pieces, i = [], 0
    min_len, keep_len = int(longer_than * 100), int(keep * 100)
    while i < n:
        j = i
        while j < n and loud[j] == loud[i]:
            j += 1
        start, end = i * hop, j * hop
        # los silencios del medio (no los del principio/final) se acortan
        if not loud[i] and j - i > min_len and i > 0 and j < n:
            half = keep_len * hop // 2
            pieces.append(audio[start : start + half])
            pieces.append(audio[end - half : end])
        else:
            pieces.append(audio[start:end])
        i = j
    pieces.append(audio[n * hop :])
    return np.concatenate(pieces)
