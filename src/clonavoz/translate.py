"""Traducción de texto 100% local, con CTranslate2 en int8.

Para los pares de idiomas que lo tienen (español-inglés y muchos otros), se usa
Opus-MT (Universidad de Helsinki, licencia Apache-2.0): un traductor chico,
de un par de idiomas, que en nuestras pruebas tradujo igual o mejor que
NLLB-200 y 5 veces más rápido (~0.1 s por frase contra ~0.5 s), lo que cuenta
mucho para traducir en vivo. Se convierte una sola vez al descargarlo.

Para el resto, NLLB-200 (Meta), que cubre alrededor
de 200 idiomas, ejecutado con CTranslate2 en int8: traduce igual y a la misma
velocidad que PyTorch, pero usa ~0.6 GB de memoria en vez de ~5 GB, lo que
lo hace viable en computadoras de bajos recursos. Usa GPU (CUDA) si hay una.

El modelo ya convertido se descarga la primera vez desde Hugging Face (con
una revisión fija). Si esa descarga no está disponible, se convierte el
modelo original una sola vez (eso sí necesita varios GB de RAM y unos
minutos). El modelo se carga una sola vez por proceso y se reutiliza para
todas las traducciones, aunque cambien los pares de idiomas.
"""
from __future__ import annotations

import gc
import shutil
import tempfile
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


_OPUS_REPO = "Helsinki-NLP/opus-mt-{}-{}"
_OPUS_TOKENIZER_FILES = ("source.spm", "target.spm", "vocab.json", "tokenizer_config.json")


def _opus_dir(source: str, target: str):
    return data_dir() / "modelos" / f"opus-mt-{source}-{target}"


def opus_ready(source: str, target: str) -> bool:
    return (_opus_dir(source, target) / "model.bin").exists()


def download_opus(source: str, target: str) -> bool:
    """Descarga y convierte (una vez) el traductor rápido de ese par de idiomas.
    False si no existe para ese par (se usa NLLB-200)."""
    if opus_ready(source, target):
        return True
    from huggingface_hub import model_info, snapshot_download
    from huggingface_hub.utils import RepositoryNotFoundError

    repo = _OPUS_REPO.format(source, target)
    try:
        files = {sibling.rfilename for sibling in model_info(repo).siblings}
    except RepositoryNotFoundError:
        return False
    # Solo lo necesario para convertirlo (el repositorio trae también los pesos
    # para TensorFlow y Rust), en una carpeta temporal: después no hace falta.
    weights = "model.safetensors" if "model.safetensors" in files else "pytorch_model.bin"
    tokenizer_files = [name for name in _OPUS_TOKENIZER_FILES if name in files]
    folder = _opus_dir(source, target)
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
            warnings.filterwarnings("ignore", message=".*sacremoses")  # ver _OpusTranslator
            converter.convert(str(folder), quantization="int8", force=True)
    finally:
        transformers_logging.set_verbosity(verbosity)
        # En Windows no se puede borrar un archivo que todavía está abierto:
        # primero se libera el modelo original que se usó para convertir.
        gc.collect()
        shutil.rmtree(tmp, ignore_errors=True)
    return True


class _OpusTranslator:
    def __init__(self, source: str, target: str, device: str) -> None:
        from transformers import MarianTokenizer

        folder = str(_opus_dir(source, target))
        with warnings.catch_warnings():
            # Sin el paquete sacremoses (normaliza comillas y guiones raros, que
            # el reconocimiento de voz no produce), transformers avisa
            # "Recommended: pip install sacremoses" en cada arranque.
            warnings.filterwarnings("ignore", message=".*sacremoses")
            self._tokenizer = MarianTokenizer.from_pretrained(folder)
        self._model = ctranslate2.Translator(
            folder, device=device, compute_type="int8_float16" if device == "cuda" else "int8", intra_threads=2
        )

    def translate(self, text: str) -> str:
        tokens = self._tokenizer.convert_ids_to_tokens(self._tokenizer.encode(text))
        result = self._model.translate_batch(
            [tokens],
            beam_size=1,
            # Si algo sale mal (por ejemplo, llega un texto mal reconocido), que no
            # diga una tira de letras repetidas: sin repetir 4 pedazos seguidos y
            # no mucho más largo que el original.
            no_repeat_ngram_size=4,
            max_decoding_length=min(256, 2 * len(tokens) + 8),
        )
        ids = self._tokenizer.convert_tokens_to_ids(result[0].hypotheses[0])
        return self._tokenizer.decode(ids, skip_special_tokens=True).strip()


def _load_model(device: str) -> None:
    global _model, _tokenizer
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
        self._opus = None
        if pair is not None and opus_ready(*pair):
            self._opus = _OpusTranslator(*pair, self.device)
            self.name = f"Opus-MT ({pair[0]} -> {pair[1]})"
            return
        self.name = "NLLB-200"
        _load_model(self.device)

    def translate(self, text: str) -> str:
        if not text.strip():
            return ""
        if self._opus is not None:
            return self._opus.translate(text)
        _tokenizer.src_lang = self.from_code
        tokens = _tokenizer.convert_ids_to_tokens(_tokenizer.encode(text))
        result = _model.translate_batch(
            [tokens], target_prefix=[[self.to_code]], beam_size=1, max_decoding_length=256
        )
        output_ids = _tokenizer.convert_tokens_to_ids(result[0].hypotheses[0][1:])
        return _tokenizer.decode(output_ids, skip_special_tokens=True).strip()
