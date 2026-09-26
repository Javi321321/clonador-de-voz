"""Traducción de texto 100% local, con CTranslate2 en int8.

Siempre que se puede se usa Opus-MT (Universidad de Helsinki, licencia
Apache-2.0): traductores chicos que en nuestras pruebas tradujeron igual que
NLLB-200 y 3 a 5 veces más rápido, lo que cuenta mucho para traducir en vivo.
Para cada par de idiomas se busca, en este orden:

- un traductor de ese par (ej. español -> inglés);
- uno multilingüe: del inglés a los idiomas romances (portugués, francés,
  italiano...) o de ellos al inglés;
- dos pasando por el inglés: para español <-> portugués, que no tiene uno
  propio. En nuestras pruebas (FLORES, 100 oraciones) tradujo igual o mejor
  que NLLB-200 (chrF++ 52.4 contra 52.2 de español a portugués; 50.1 contra
  50.8 al revés), 3 veces más rápido y en portugués de Brasil ("Ei, como vai?
  Queria te dizer uma coisa.").

Se descargan y convierten una sola vez (`download_opus`).

Para el resto, NLLB-200 (Meta), que cubre alrededor de 200 idiomas, con
CTranslate2 en int8: traduce igual y a la misma velocidad que PyTorch, pero
usa ~0.6 GB de memoria en vez de ~5 GB. Usa GPU (CUDA) si hay una. Se carga
recién cuando hace falta (un idioma sin Opus-MT) y una sola vez por proceso.

El modelo ya convertido se descarga la primera vez desde Hugging Face (con
una revisión fija). Si esa descarga no está disponible, se convierte el
modelo original una sola vez (eso sí necesita varios GB de RAM y unos
minutos).

Las frases se traducen de a una oración (todas juntas, en un mismo lote): con
dos oraciones seguidas, estos traductores a veces se salteaban una ("Hola,
¿cómo estás? Te quería contar algo." salía "Olá, como estás?").
"""
from __future__ import annotations

import gc
import re
import shutil
import tempfile
import threading
import warnings

# En Windows, torch y ctranslate2 traen cada uno su copia de libiomp5md.dll
# (OpenMP): cargando torch primero, ctranslate2 reutiliza esa misma copia en vez
# de cargar una segunda, que cerraría el programa con "OMP: Error #15".
import torch  # noqa: F401  (ver arriba: tiene que importarse antes que ctranslate2)
import ctranslate2
from transformers import AutoTokenizer
from transformers.utils import logging as transformers_logging

from .paths import data_dir

_ORIGINAL_MODEL = "facebook/nllb-200-distilled-600M"
_ORIGINAL_REVISION = "f8d333a098d19b4fd9a8b18f94170487ad3f821d"
_CONVERTED_MODEL = "JustFrederik/nllb-200-distilled-600M-ct2-int8"
_CONVERTED_REVISION = "302d78f00e6fdb50a1064059df7c392b735e9d05"

_model = None
_tokenizer = None


def _converted_model_dir() -> str:
    from huggingface_hub import snapshot_download

    try:
        return snapshot_download(_CONVERTED_MODEL, revision=_CONVERTED_REVISION)
    except Exception as exc:  # noqa: BLE001 - sin esa descarga, se convierte localmente
        local = data_dir() / "modelos" / "nllb-200-distilled-600M-ct2-int8"
        if (local / "model.bin").exists():
            return str(local)
        print(f"[clonavoz] No se pudo descargar el traductor ya convertido ({exc}).")
        print("[clonavoz] Convirtiendo NLLB-200 a CTranslate2 (una sola vez, puede tardar unos minutos)...")
        converter = ctranslate2.converters.TransformersConverter(_ORIGINAL_MODEL, revision=_ORIGINAL_REVISION)
        converter.convert(str(local), quantization="int8")
        return str(local)


def download() -> None:
    """Deja el traductor descargado para poder usarlo sin internet."""
    AutoTokenizer.from_pretrained(_ORIGINAL_MODEL, revision=_ORIGINAL_REVISION)
    _converted_model_dir()


# Fin de oración: después de . ! ? o …, antes de una mayúscula (o un número,
# o signos de apertura como ¿ ¡ « ").
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+(?=[¿¡\"'«(]?[A-ZÁÉÍÓÚÑÂÊÎÔÛÃÕÇÀÈÌÒÙÄËÏÖÜ0-9])")


def split_sentences(text: str) -> list[str]:
    return [part for part in _SENTENCE_END.split(text.strip()) if part]


_OPUS_REPO = "Helsinki-NLP/opus-mt-{}"
_OPUS_TOKENIZER_FILES = ("source.spm", "target.spm", "vocab.json", "tokenizer_config.json")
# Los multilingües: "ROMANCE-en" traduce de estos idiomas al inglés y
# "en-ROMANCE" del inglés a ellos (con el idioma destino al principio, como
# ">>pt_br<<": el portugués sale de Brasil, que es el más probable al hablar
# con alguien de Sudamérica).
_ROMANCE = {"es", "pt", "fr", "it", "ro", "ca", "gl"}
_ROMANCE_TOKENS = {"pt": ">>pt_br<<"}


def _routes(source: str, target: str) -> list[list[tuple[str, str]]]:
    """Formas de traducir de `source` a `target` con Opus-MT, de mejor a peor.
    Cada una es una lista de pasos (modelo, prefijo): uno, o dos pasando por
    el inglés."""
    direct = [[(f"{source}-{target}", "")]]
    if target == "en" and source in _ROMANCE:
        direct.append([("ROMANCE-en", "")])
    if source == "en" and target in _ROMANCE:
        direct.append([("en-ROMANCE", _ROMANCE_TOKENS.get(target, f">>{target}<<") + " ")])
    if "en" in (source, target):
        return direct
    return direct + [first + second for first in _routes(source, "en") for second in _routes("en", target)]


def _opus_dir(name: str):
    return data_dir() / "modelos" / f"opus-mt-{name}"


def opus_model_ready(name: str) -> bool:
    return (_opus_dir(name) / "model.bin").exists()


def opus_route(source: str, target: str) -> list[tuple[str, str]] | None:
    """Los pasos de Opus-MT ya descargados para ese par, o None (se usa NLLB-200)."""
    for route in _routes(source, target):
        if all(opus_model_ready(name) for name, _ in route):
            return route
    return None


def opus_ready(source: str, target: str) -> bool:
    return opus_route(source, target) is not None


_exists: dict[str, bool] = {}


def _opus_exists(name: str) -> bool:
    """Si ese Opus-MT existe en Hugging Face (usa internet; ya descargado: True)."""
    if opus_model_ready(name):
        return True
    if name not in _exists:
        from huggingface_hub import model_info
        from huggingface_hub.utils import RepositoryNotFoundError

        try:
            model_info(_OPUS_REPO.format(name))
            _exists[name] = True
        except RepositoryNotFoundError:
            _exists[name] = False
    return _exists[name]


def download_opus(source: str, target: str) -> list[str] | None:
    """Descarga y convierte (una vez) los traductores rápidos para ese par de
    idiomas. Devuelve sus nombres (ej. ["es-en", "en-ROMANCE"], si pasa por el
    inglés), o None si no hay (se usa NLLB-200)."""
    for route in _routes(source, target):
        if all(_opus_exists(name) for name, _ in route):
            for name, _ in route:
                _download_opus_model(name)
            return [name for name, _ in route]
    return None


def _download_opus_model(name: str) -> None:
    if opus_model_ready(name):
        return
    from huggingface_hub import model_info, snapshot_download

    repo = _OPUS_REPO.format(name)
    files = {sibling.rfilename for sibling in model_info(repo).siblings}
    # Solo lo necesario para convertirlo (el repositorio trae también los pesos
    # para TensorFlow y Rust), en una carpeta temporal: después no hace falta.
    weights = "model.safetensors" if "model.safetensors" in files else "pytorch_model.bin"
    tokenizer_files = [file for file in _OPUS_TOKENIZER_FILES if file in files]
    folder = _opus_dir(name)
    folder.parent.mkdir(parents=True, exist_ok=True)
    for leftover in folder.parent.glob(".descarga-*"):  # de una descarga que se cortó
        shutil.rmtree(leftover, ignore_errors=True)
    tmp = tempfile.mkdtemp(prefix=".descarga-", dir=folder.parent)
    verbosity = transformers_logging.get_verbosity()
    transformers_logging.set_verbosity_error()  # avisos internos de transformers al convertir
    try:
        snapshot_download(
            repo, local_dir=tmp, allow_patterns=["config.json", "generation_config.json", weights, *tokenizer_files]
        )
        converter = ctranslate2.converters.TransformersConverter(tmp, copy_files=tokenizer_files)
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message=".*sacremoses")  # ver _OpusModel
            converter.convert(str(folder), quantization="int8", force=True)
    finally:
        transformers_logging.set_verbosity(verbosity)
        # En Windows no se puede borrar un archivo que todavía está abierto:
        # primero se libera el modelo original que se usó para convertir.
        gc.collect()
        shutil.rmtree(tmp, ignore_errors=True)


class _OpusModel:
    """Un Opus-MT cargado. Se comparte entre traductores (ej. el de español a
    inglés sirve también para pasar del español al portugués)."""

    def __init__(self, name: str, device: str) -> None:
        from transformers import MarianTokenizer

        folder = str(_opus_dir(name))
        with warnings.catch_warnings():
            # Sin el paquete sacremoses (normaliza comillas y guiones raros, que
            # el reconocimiento de voz no produce), transformers avisa
            # "Recommended: pip install sacremoses" en cada arranque.
            warnings.filterwarnings("ignore", message=".*sacremoses")
            self._tokenizer = MarianTokenizer.from_pretrained(folder)
        self._model = ctranslate2.Translator(
            folder, device=device, compute_type="int8_float16" if device == "cuda" else "int8", intra_threads=2
        )

    def translate(self, sentences: list[str], prefix: str = "") -> list[str]:
        batch = [self._tokenizer.convert_ids_to_tokens(self._tokenizer.encode(prefix + text)) for text in sentences]
        results = self._model.translate_batch(
            batch,
            beam_size=1,
            # Si algo sale mal (por ejemplo, llega un texto mal reconocido), que no
            # diga una tira de letras repetidas: sin repetir 4 pedazos seguidos y
            # no mucho más largo que el original.
            no_repeat_ngram_size=4,
            max_decoding_length=min(256, 2 * max(len(tokens) for tokens in batch) + 8),
        )
        tokenizer = self._tokenizer
        return [
            tokenizer.decode(tokenizer.convert_tokens_to_ids(result.hypotheses[0]), skip_special_tokens=True).strip()
            for result in results
        ]


_opus_models: dict[tuple[str, str], _OpusModel] = {}
_opus_lock = threading.Lock()


def _opus_model(name: str, device: str) -> _OpusModel:
    with _opus_lock:
        if (name, device) not in _opus_models:
            _opus_models[(name, device)] = _OpusModel(name, device)
        return _opus_models[(name, device)]


_nllb_lock = threading.Lock()  # el tokenizador se configura para cada idioma de origen


def _load_model(device: str) -> None:
    global _model, _tokenizer
    with _nllb_lock:
        if _model is not None:
            return
        _tokenizer = AutoTokenizer.from_pretrained(_ORIGINAL_MODEL, revision=_ORIGINAL_REVISION)
        compute_type = "int8_float16" if device == "cuda" else "int8"
        _model = ctranslate2.Translator(_converted_model_dir(), device=device, compute_type=compute_type)


class Translator:
    """`pair`: los códigos cortos de los dos idiomas (ej. ("es", "en")), para
    usar Opus-MT si está descargado para ese par; si no, NLLB-200."""

    def __init__(
        self, from_nllb_code: str, to_nllb_code: str, device: str = "cpu", pair: tuple[str, str] | None = None
    ) -> None:
        self.from_code = from_nllb_code
        self.to_code = to_nllb_code
        self.device = "cuda" if device == "cuda" else "cpu"
        self._steps: list[tuple[_OpusModel, str]] = []
        route = opus_route(*pair) if pair is not None and pair[0] != pair[1] else None
        if route is not None:
            self._steps = [(_opus_model(name, self.device), prefix) for name, prefix in route]
            languages = [pair[0], *("en" for _ in route[1:]), pair[1]]
            self.name = f"Opus-MT ({' -> '.join(languages)})"
            return
        self.name = "NLLB-200"
        _load_model(self.device)

    def translate(self, text: str) -> str:
        sentences = split_sentences(text)
        if not sentences:
            return ""
        for model, prefix in self._steps:
            sentences = model.translate(sentences, prefix)
        if not self._steps:
            sentences = self._nllb(sentences)
        return " ".join(sentence for sentence in sentences if sentence)

    def _nllb(self, sentences: list[str]) -> list[str]:
        with _nllb_lock:
            _tokenizer.src_lang = self.from_code
            batch = [_tokenizer.convert_ids_to_tokens(_tokenizer.encode(text)) for text in sentences]
        results = _model.translate_batch(
            batch, target_prefix=[[self.to_code]] * len(batch), beam_size=1, max_decoding_length=256
        )
        # (cada resultado empieza con el código del idioma destino)
        ids = [_tokenizer.convert_tokens_to_ids(result.hypotheses[0][1:]) for result in results]
        return [_tokenizer.decode(sentence, skip_special_tokens=True).strip() for sentence in ids]
