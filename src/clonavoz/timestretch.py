"""Hablar más rápido sin cambiar el tono de la voz, para que la traducción
alcance a quien habla cuando se atrasa (como hace un intérprete).

- `speed_up`: WSOLA (superposición de pedazos elegidos por parecido), el
  método clásico para acelerar voz sin que suene "de ardilla". Hasta ~1.3x
  casi no se nota.
- `shorten_pauses`: acorta los silencios largos dentro de una frase.
"""
from __future__ import annotations

import numpy as np


def speed_up(audio: np.ndarray, sample_rate: int, speed: float) -> np.ndarray:
    """`audio` más rápido (`speed` > 1), con el mismo tono."""
    audio = np.asarray(audio, dtype=np.float32)
    frame = int(0.030 * sample_rate)  # ventanas de 30 ms
    if speed <= 1.001 or len(audio) < 4 * frame:
        return audio
    hop_out = frame // 2
    hop_in = hop_out * speed
    search = int(0.012 * sample_rate)
    window = np.hanning(frame).astype(np.float32)
    n_frames = int((len(audio) - frame - search) / hop_in)
    out = np.zeros(n_frames * hop_out + frame, dtype=np.float32)
    norm = np.zeros_like(out)
    previous = 0  # dónde empezó en `audio` la ventana anterior elegida
    for k in range(n_frames):
        ideal = int(k * hop_in)
        if k == 0:
            start = 0
        else:
            # lo que "seguiría naturalmente" a la ventana anterior, y se busca
            # cerca de la posición ideal el pedazo que mejor empalma con eso
            natural = audio[previous + hop_out : previous + hop_out + frame]
            lo, hi = max(0, ideal - search), min(len(audio) - frame, ideal + search)
            region = audio[lo : hi + frame]
            if len(natural) < frame or hi <= lo:
                start = min(max(0, ideal), len(audio) - frame)
            else:
                scores = np.correlate(region, natural, mode="valid")
                start = lo + int(np.argmax(scores))
        segment = audio[start : start + frame]
        out[k * hop_out : k * hop_out + frame] += segment * window
        norm[k * hop_out : k * hop_out + frame] += window
        previous = start
    norm[norm < 1e-3] = 1.0
    return out / norm


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
