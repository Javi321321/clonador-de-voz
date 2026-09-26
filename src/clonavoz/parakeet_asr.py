"""Reconocimiento de voz con Parakeet TDT 0.6B v3 (NVIDIA, 2025; modelo
CC-BY-4.0) en su versión ONNX int8, con onnx-asr (MIT). Entiende 25 idiomas
europeos, entre ellos español e inglés, y pone puntuación.

En nuestras pruebas en español se equivocó mucho menos que Whisper y es igual
de rápido que el Whisper más chico: en 10 minutos de voz real (40 grabaciones
de 5 personas), 3.5% de palabras mal, contra 19.4% de Whisper "tiny" y 5.0% de
Whisper "small"; en frases cortas, 2.7% contra 10.3% y 5.5%. Con 2 núcleos
reconoce una frase de 3 s en ~0.4 s (como tiny; small tarda ~1.7 s).

Usa unos 900 MB de memoria, así que solo se usa en computadoras con al menos
6 GB de RAM; en las demás, y en los idiomas que no entiende, sigue Whisper.
"""
from __future__ import annotations

import threading
from pathlib import Path

import numpy as np
import psutil

# Códigos de idioma de clonavoz que entiende (los 25 de Parakeet v3).
LANGUAGES = {
    "bg", "hr", "cs", "da", "nl", "en", "et", "fi", "fr", "de", "el", "hu", "it",
    "lv", "lt", "mt", "pl", "pt", "ro", "sk", "sl", "es", "sv", "ru", "uk",
}
MIN_RAM_GB = 6
_REPO = "istupakov/parakeet-tdt-0.6b-v3-onnx"
_REVISION = "8f23f0c03c8761650bdb5b40aaf3e40d2c15f1ce"
_FILES = ["config.json", "vocab.txt", "nemo128.onnx", "encoder-model.int8.onnx", "decoder_joint-model.int8.onnx"]


def supports(language_code: str) -> bool:
    return language_code in LANGUAGES


def enough_memory() -> bool:
    return psutil.virtual_memory().total / 1024**3 >= MIN_RAM_GB - 0.5  # "8 GB" suele reportar ~7.8


def _snapshot(local_only: bool) -> Path:
    from huggingface_hub import snapshot_download

    return Path(snapshot_download(_REPO, revision=_REVISION, allow_patterns=_FILES, local_files_only=local_only))


def ready() -> bool:
    """True si el modelo ya está descargado (no usa internet)."""
    try:
        folder = _snapshot(local_only=True)
    except Exception:  # noqa: BLE001 - sin descargar (o sin onnx-asr): se usa Whisper
        return False
    return all((folder / name).exists() for name in _FILES)


def usable(language_code: str) -> bool:
    return supports(language_code) and enough_memory() and ready()


def download() -> None:
    _snapshot(local_only=False)


_shared: ParakeetRecognizer | None = None
_shared_lock = threading.Lock()


def shared(threads: int) -> ParakeetRecognizer:
    """Uno solo por programa: entiende todos sus idiomas, así que lo usan a la
    vez lo que decís vos y lo que te dicen (900 MB de memoria una sola vez)."""
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = ParakeetRecognizer(threads)
        return _shared


class ParakeetRecognizer:
    def __init__(self, threads: int) -> None:
        import onnx_asr
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        # Sin esto, ONNX Runtime duplica los pesos para acelerar: +400 MB de
        # memoria para ganar ~5% de velocidad.
        options.add_session_config_entry("session.disable_prepacking", "1")
        self._model = onnx_asr.load_model(
            "nemo-parakeet-tdt-0.6b-v3", path=_snapshot(local_only=True), quantization="int8", sess_options=options
        )
        self._timed = self._model.with_timestamps()
        self._lock = threading.Lock()  # de a una frase por vez (tu voz y la de los demás)

    def transcribe(self, audio: np.ndarray) -> str:
        with self._lock:
            return str(self._model.recognize(np.asarray(audio, dtype=np.float32), sample_rate=16000)).strip()

    def transcribe_timed(self, audio: np.ndarray) -> tuple[list[str], list[float]]:
        """Los pedazos de texto reconocidos (las palabras empiezan con espacio;
        comas y puntos van aparte) y en qué segundo empieza cada uno."""
        with self._lock:
            result = self._timed.recognize(np.asarray(audio, dtype=np.float32), sample_rate=16000)
        return list(result.tokens or []), [float(t) for t in (result.timestamps or [])]
