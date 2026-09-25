"""Traducción de texto 100% local con NLLB-200 (Meta), que cubre alrededor
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

# En Windows, torch y ctranslate2 traen cada uno su copia de libiomp5md.dll
# (OpenMP): cargando torch primero, ctranslate2 reutiliza esa misma copia en vez
# de cargar una segunda, que cerraría el programa con "OMP: Error #15".
import torch  # noqa: F401  (ver arriba: tiene que importarse antes que ctranslate2)
import ctranslate2
from transformers import AutoTokenizer

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


def _load_model(device: str) -> None:
    global _model, _tokenizer
    if _model is not None:
        return
    _tokenizer = AutoTokenizer.from_pretrained(_ORIGINAL_MODEL, revision=_ORIGINAL_REVISION)
    compute_type = "int8_float16" if device == "cuda" else "int8"
    _model = ctranslate2.Translator(_converted_model_dir(), device=device, compute_type=compute_type)


class Translator:
    def __init__(self, from_nllb_code: str, to_nllb_code: str, device: str = "cpu") -> None:
        self.from_code = from_nllb_code
        self.to_code = to_nllb_code
        self.device = "cuda" if device == "cuda" else "cpu"
        _load_model(self.device)

    def translate(self, text: str) -> str:
        if not text.strip():
            return ""
        _tokenizer.src_lang = self.from_code
        tokens = _tokenizer.convert_ids_to_tokens(_tokenizer.encode(text))
        result = _model.translate_batch(
            [tokens], target_prefix=[[self.to_code]], beam_size=1, max_decoding_length=256
        )
        output_ids = _tokenizer.convert_tokens_to_ids(result[0].hypotheses[0][1:])
        return _tokenizer.decode(output_ids, skip_special_tokens=True).strip()
