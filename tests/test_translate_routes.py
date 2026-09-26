"""Qué traductor se usa para cada par de idiomas (sin modelos: piezas falsas)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from clonavoz import translate  # noqa: E402


def test_sentences_are_translated_one_by_one():
    assert translate.split_sentences("Hola, ¿cómo estás? Te quería contar algo.") == [
        "Hola, ¿cómo estás?",
        "Te quería contar algo.",
    ]
    assert translate.split_sentences("¡Qué bueno! ¿Venís? Dale.") == ["¡Qué bueno!", "¿Venís?", "Dale."]
    # un número con punto o puntos suspensivos sin mayúscula después no cortan
    assert translate.split_sentences("Son las 3.5 horas... ok") == ["Son las 3.5 horas... ok"]
    assert translate.split_sentences("  ") == []


def test_portuguese_goes_through_english():
    routes = translate._routes("es", "pt")
    assert routes[0] == [("es-pt", "")]  # uno propio, si existiera
    assert [("es-en", ""), ("en-ROMANCE", ">>pt_br<< ")] in routes
    assert translate._routes("pt", "en") == [[("pt-en", "")], [("ROMANCE-en", "")]]
    assert translate._routes("en", "pt")[-1] == [("en-ROMANCE", ">>pt_br<< ")]


def test_the_first_downloaded_route_is_used(monkeypatch):
    ready = {"es-en", "en-ROMANCE", "ROMANCE-en", "en-es"}
    monkeypatch.setattr(translate, "opus_model_ready", lambda name: name in ready)
    assert translate.opus_route("es", "pt") == [("es-en", ""), ("en-ROMANCE", ">>pt_br<< ")]
    assert translate.opus_route("pt", "es") == [("ROMANCE-en", ""), ("en-es", "")]
    assert translate.opus_route("es", "en") == [("es-en", "")]
    assert translate.opus_route("ja", "es") is None  # ese se traduce con NLLB-200


class FakeOpus:
    def __init__(self, name):
        self.name = name
        self.calls = []

    def translate(self, sentences, prefix=""):
        self.calls.append((list(sentences), prefix))
        return [f"{self.name}({prefix.strip()}{text})" for text in sentences]


def test_two_steps_translate_each_sentence(monkeypatch):
    models = {}
    monkeypatch.setattr(translate, "opus_model_ready", lambda name: name in ("es-en", "en-ROMANCE"))
    monkeypatch.setattr(translate, "_opus_model", lambda name, device: models.setdefault(name, FakeOpus(name)))
    tr = translate.Translator("spa_Latn", "por_Latn", pair=("es", "pt"))
    assert tr.name == "Opus-MT (es -> en -> pt)"
    out = tr.translate("Hola. ¿Cómo estás?")
    assert out == "en-ROMANCE(>>pt_br<<es-en(Hola.)) en-ROMANCE(>>pt_br<<es-en(¿Cómo estás?))"
    assert models["es-en"].calls == [(["Hola.", "¿Cómo estás?"], "")]  # un solo lote


def test_download_picks_a_route_that_exists(monkeypatch):
    exists = {"es-en", "en-ROMANCE"}
    downloaded = []
    monkeypatch.setattr(translate, "_opus_exists", lambda name: name in exists)
    monkeypatch.setattr(translate, "_download_opus_model", downloaded.append)
    assert translate.download_opus("es", "pt") == ["es-en", "en-ROMANCE"]
    assert downloaded == ["es-en", "en-ROMANCE"]
    assert translate.download_opus("ja", "pt") is None
