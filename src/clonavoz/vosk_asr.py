"""Reconocimiento de voz en streaming con Vosk (Kaldi; Apache-2.0), para PCs
lentas o con poca memoria. Va entendiendo mientras hablás, así que cuando
terminás una frase el texto está listo casi al instante: en una notebook lenta
~15 ms, contra ~0.7 s de Whisper, que recién empieza cuando terminás.

Los modelos chicos (~40 MB por idioma) usan poco procesador (en una notebook
lenta, 0.09 s por cada segundo de voz) y poca memoria (~250 MB). En nuestras
pruebas en español se equivocó menos que Whisper "tiny": 10.3% de palabras mal
contra 19.4% en 10 minutos de grabaciones reales, y 7.4% contra 22.1% en
frases cortas.

No pone puntuación: las preguntas se reconocen aparte (ver punctuation.py).
Parakeet (ver parakeet_asr.py) se equivoca todavía menos y traduce mientras
hablás, así que en PCs con memoria y procesador de sobra se usa ese.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path

import numpy as np

from .paths import data_dir

# Código de idioma de clonavoz -> (modelo chico oficial, md5 del zip). Todos
# Apache-2.0 (https://alphacephei.com/vosk/models). No están los idiomas con
# modelos chicos flojos (portugués) o de licencia no comercial.
MODELS = {
    "es": ("vosk-model-small-es-0.42", "2d5c94f9859a84881a0ef744738ebd31"),
    "en": ("vosk-model-small-en-us-0.15", "09ab50ccd62b674cbaa231b825f9c1cb"),
    "fr": ("vosk-model-small-fr-0.22", "8873b1234503f6edd55f54bfff31cf3e"),
    "de": ("vosk-model-small-de-0.15", "4f21f92c0897b48287ef8839420608eb"),
    "it": ("vosk-model-small-it-0.22", "fbd8f9c72cbb8c3dfa3e4581bd3585f4"),
    "nl": ("vosk-model-small-nl-0.22", "4582e1f20d6849099da08511f9797017"),
    "ru": ("vosk-model-small-ru-0.22", "d1759dc83eb8fd87850129afbd9f4b7b"),
    "pl": ("vosk-model-small-pl-0.22", "91cbbd6231320467da672be31827b6ac"),
}
_URL = "https://alphacephei.com/vosk/models/{}.zip"
SAMPLE_RATE = 16000


def supports(language_code: str) -> bool:
    return language_code in MODELS


def _model_dir(language_code: str) -> Path:
    return data_dir() / "modelos" / "vosk" / MODELS[language_code][0]


def ready(language_code: str) -> bool:
    """True si el modelo de ese idioma ya está descargado (no usa internet)."""
    if not supports(language_code):
        return False
    try:
        import vosk  # noqa: F401
    except ImportError:
        return False
    return (_model_dir(language_code) / "conf" / "model.conf").exists()


def download(language_code: str) -> None:
    """Descarga y descomprime el modelo chico de ese idioma (una vez)."""
    if ready(language_code):
        return
    name, md5 = MODELS[language_code]
    folder = _model_dir(language_code).parent
    folder.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".descarga-", dir=folder) as tmp:
        archive = Path(tmp) / f"{name}.zip"
        digest = hashlib.md5()  # noqa: S324 - es el que publica Vosk para verificar la descarga
        with urllib.request.urlopen(_URL.format(name), timeout=60) as response, open(archive, "wb") as out:
            while chunk := response.read(1 << 20):
                digest.update(chunk)
                out.write(chunk)
        if digest.hexdigest() != md5:
            raise RuntimeError(f"La descarga del reconocimiento rápido ({name}) llegó dañada: probá de nuevo.")
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(tmp)
        shutil.rmtree(_model_dir(language_code), ignore_errors=True)
        shutil.move(str(Path(tmp) / name), str(_model_dir(language_code)))


class VoskModel:
    """El modelo cargado (una vez). `stream()` da un reconocedor para ir
    alimentando mientras hablás; `transcribe` reconoce un audio entero."""

    def __init__(self, language_code: str) -> None:
        from vosk import Model, SetLogLevel

        SetLogLevel(-1)  # sin los mensajes internos de Kaldi en la consola
        self._model = Model(str(_model_dir(language_code)))

    def stream(self) -> VoskStream:
        return VoskStream(self._model)

    def transcribe(self, audio: np.ndarray) -> str:
        stream = self.stream()
        stream.accept(audio)
        return stream.text()


class VoskStream:
    def __init__(self, model) -> None:
        from vosk import KaldiRecognizer

        self._recognizer = KaldiRecognizer(model, SAMPLE_RATE)
        self._parts: list[str] = []

    def accept(self, audio: np.ndarray) -> None:
        """Un pedazo de audio (float, 16 kHz), a medida que llega."""
        pcm = (np.clip(np.asarray(audio, dtype=np.float32), -1.0, 1.0) * 32767).astype(np.int16).tobytes()
        if self._recognizer.AcceptWaveform(pcm):
            # Vosk cerró un tramo por su cuenta (una pausa larga): se guarda.
            self._parts.append(json.loads(self._recognizer.Result()).get("text", ""))

    def text(self) -> str:
        """Todo lo reconocido desde la vez anterior (y empieza de cero)."""
        self._parts.append(json.loads(self._recognizer.FinalResult()).get("text", ""))
        text = " ".join(part for part in self._parts if part).strip()
        self._parts = []
        return text
