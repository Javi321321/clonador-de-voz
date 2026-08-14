"""Traducción de texto 100% local con NLLB-200 (Meta), que cubre alrededor
de 200 idiomas. Se cuantiza dinámicamente a int8 en CPU para que sea viable
en equipos de bajos recursos; usa GPU automáticamente si hay una disponible.

El modelo se carga una sola vez por proceso (es pesado) y se reutiliza para
todas las traducciones, aunque cambien los pares de idiomas de origen/destino.
"""
from __future__ import annotations

import torch
from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

_MODEL_NAME = "facebook/nllb-200-distilled-600M"

_model = None
_tokenizer = None


def _load_model(device: str, quantize: bool) -> None:
    global _model, _tokenizer
    if _model is not None:
        return
    _tokenizer = AutoTokenizer.from_pretrained(_MODEL_NAME)
    model = AutoModelForSeq2SeqLM.from_pretrained(_MODEL_NAME)
    if quantize and device == "cpu":
        model = torch.quantization.quantize_dynamic(model, {torch.nn.Linear}, dtype=torch.qint8)
    model.to(device)
    model.eval()
    _model = model


class Translator:
    def __init__(self, from_nllb_code: str, to_nllb_code: str, device: str = "cpu") -> None:
        self.from_code = from_nllb_code
        self.to_code = to_nllb_code
        self.device = device if device in ("cuda", "cpu") else "cpu"
        _load_model(self.device, quantize=self.device == "cpu")

    @torch.inference_mode()
    def translate(self, text: str) -> str:
        if not text.strip():
            return ""
        _tokenizer.src_lang = self.from_code
        inputs = _tokenizer(text, return_tensors="pt").to(self.device)
        forced_bos_token_id = _tokenizer.convert_tokens_to_ids(self.to_code)
        generated = _model.generate(
            **inputs,
            forced_bos_token_id=forced_bos_token_id,
            max_new_tokens=256,
            num_beams=1,
        )
        return _tokenizer.batch_decode(generated, skip_special_tokens=True)[0].strip()
