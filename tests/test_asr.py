"""Qué reconocimiento de voz se usa: Parakeet si entiende el idioma, está
descargado y hay memoria; si no, Whisper. Sin descargar ningún modelo."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from clonavoz import asr, parakeet_asr  # noqa: E402
from clonavoz.config import get_profile  # noqa: E402


class FakeParakeet:
    def __init__(self, threads):
        self.threads = threads

    def transcribe(self, audio):
        return "hola"


class FakeWhisper:
    def __init__(self, size, **_kwargs):
        self.size = size


def _setup(monkeypatch, ready=True, memory=True):
    monkeypatch.setattr(parakeet_asr, "ready", lambda: ready)
    monkeypatch.setattr(parakeet_asr, "enough_memory", lambda: memory)
    monkeypatch.setattr(parakeet_asr, "ParakeetRecognizer", FakeParakeet)
    monkeypatch.setattr(asr, "WhisperModel", FakeWhisper)


def test_parakeet_for_spanish_when_downloaded_and_there_is_memory(monkeypatch):
    _setup(monkeypatch)
    recognizer = asr.SpeechRecognizer(get_profile("low"), "es")
    assert recognizer.name == "Parakeet"
    assert recognizer.transcribe(None, "es") == "hola"


def test_whisper_for_a_language_parakeet_does_not_understand(monkeypatch):
    _setup(monkeypatch)
    assert asr.SpeechRecognizer(get_profile("low"), "ja").name == "Whisper tiny"


def test_whisper_when_not_downloaded_or_little_memory(monkeypatch):
    _setup(monkeypatch, ready=False)
    assert asr.SpeechRecognizer(get_profile("low"), "es").name == "Whisper tiny"
    _setup(monkeypatch, memory=False)
    assert asr.SpeechRecognizer(get_profile("medium"), "es").name == "Whisper small"


def test_without_a_language_it_is_whisper(monkeypatch):
    _setup(monkeypatch)
    assert asr.SpeechRecognizer(get_profile("low")).name == "Whisper tiny"


def test_parakeet_languages():
    assert {"es", "en", "pt", "fr", "de", "it", "ru", "uk"} <= parakeet_asr.LANGUAGES
    assert not parakeet_asr.supports("zh")
