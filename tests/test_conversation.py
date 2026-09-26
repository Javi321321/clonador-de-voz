"""Conversación en las dos direcciones: te escuchan en el idioma en que te
hablan, y tu micrófono no se escucha mientras suena lo que te dijeron."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import fake_sounddevice  # noqa: E402

fake_sounddevice.install()

from clonavoz import pipeline  # noqa: E402
from clonavoz.languages import get_language  # noqa: E402


class FakeTranslator:
    def __init__(self, source, target, device="cpu", pair=None):
        self.pair = pair
        self.name = f"falso {pair}"


class FakeSynth:
    def __init__(self):
        self.loaded = []

    def preload(self, language):
        if language.code == "xx":
            raise RuntimeError("sin voz")
        self.loaded.append(language.code)


def outgoing(monkeypatch):
    monkeypatch.setattr(pipeline, "Translator", FakeTranslator)
    monkeypatch.setattr(pipeline, "get_language", get_language)
    p = object.__new__(pipeline.LiveVoicePipeline)
    p.source_language = get_language("es")
    p.target_language = get_language("en")
    p._translator = FakeTranslator(None, None, pair=("es", "en"))
    p._synth = FakeSynth()
    p.profile = type("Profile", (), {"device": "cpu"})()
    p._targets = {"en": (p.target_language, p._translator)}
    p.messages = []
    p.on_message = p.messages.append
    p.muted_while = None
    return p


def test_you_are_heard_in_the_language_they_speak(monkeypatch):
    p = outgoing(monkeypatch)
    p.prepare_languages(["en", "pt", "es"])  # el tuyo y el que ya estaba se saltean
    assert p._synth.loaded == ["pt"]
    assert p.switch_target("pt")
    assert p.target_language.code == "pt" and p._translator.pair == ("es", "pt")
    assert not p.switch_target("fr")  # sin preparar: sigue igual
    assert p.target_language.code == "pt"
    assert p.switch_target("en") and p._translator.pair == ("es", "en")


def test_a_language_without_voice_is_skipped_with_a_notice(monkeypatch):
    p = outgoing(monkeypatch)
    monkeypatch.setattr(pipeline, "get_language", lambda code: type("L", (), {"code": code, "nllb_code": code})())
    p.prepare_languages(["xx"])
    assert "xx" not in p._targets and any("xx" in m for m in p.messages)


def test_the_microphone_is_muted_while_their_translation_sounds(monkeypatch):
    p = outgoing(monkeypatch)
    frame = np.ones(512, dtype=np.float32)
    assert p._filter_frame(frame).all()
    p.muted_while = lambda: True
    assert not p._filter_frame(frame).any()
