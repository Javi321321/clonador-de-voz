"""Detección de actividad de voz en streaming (Silero VAD) para cortar el
audio del micrófono en frases habladas naturales apenas termina una pausa,
en lugar de esperar un buffer fijo largo. Esto es lo que mantiene la
latencia baja: cada frase se procesa tan pronto como se termina de decir.
"""
from __future__ import annotations

import numpy as np
import torch

_FRAME_SAMPLES = 512  # ventana que espera silero-vad a 16kHz
_SILENCE_MS_TO_END = 500


class StreamingVAD:
    """Recibe audio en bloques pequeños (`frame_size()` muestras) y libera
    un arreglo numpy con cada frase hablada completa apenas detecta una
    pausa, o al llegar a `max_utterance_seconds` si alguien habla sin pausas."""

    def __init__(self, max_utterance_seconds: float = 5.0) -> None:
        self._model, utils = torch.hub.load(
            repo_or_dir="snakers4/silero-vad",
            model="silero_vad",
            trust_repo=True,
            onnx=False,
        )
        (_, _, _, vad_iterator_cls, _) = utils
        self._iterator = vad_iterator_cls(
            self._model,
            sampling_rate=16000,
            min_silence_duration_ms=_SILENCE_MS_TO_END,
        )
        self._buffer: list[np.ndarray] = []
        self._speaking = False
        self._max_samples = int(max_utterance_seconds * 16000)

    def push(self, frame: np.ndarray) -> np.ndarray | None:
        event = self._iterator(torch.from_numpy(frame), return_seconds=False)

        if event and "start" in event:
            self._speaking = True
            self._buffer = [frame]
        elif self._speaking:
            self._buffer.append(frame)

        if event and "end" in event and self._speaking:
            return self._flush()

        if self._speaking and sum(len(b) for b in self._buffer) >= self._max_samples:
            return self._flush()

        return None

    def _flush(self) -> np.ndarray:
        self._speaking = False
        utterance = np.concatenate(self._buffer) if self._buffer else np.array([], dtype=np.float32)
        self._buffer = []
        self._iterator.reset_states()
        return utterance

    @staticmethod
    def frame_size() -> int:
        return _FRAME_SAMPLES
