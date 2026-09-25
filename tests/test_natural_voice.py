"""Pruebas de la voz natural (Pocket TTS) sin descargar el modelo: qué motor
se elige según lo que esté descargado, y que revisar si está disponible no
cambie los hilos de torch (importar pocket_tts los deja en 1)."""
import sys
from pathlib import Path

import pytest
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from clonavoz import pocket_voice, voice_clone  # noqa: E402
from clonavoz.languages import get_language  # noqa: E402
from clonavoz.voice_clone import choose_engine  # noqa: E402

EN, UK = get_language("en"), get_language("uk")


@pytest.fixture
def downloaded(monkeypatch):
    """Simula qué idiomas tienen la voz natural descargada."""
    ready: set[str] = set()
    monkeypatch.setattr(pocket_voice, "cloning_ready", lambda code: code in ready)
    monkeypatch.setattr(voice_clone, "xtts_installed", lambda: False)
    return ready


def test_auto_uses_the_natural_voice_when_it_is_downloaded(downloaded):
    downloaded.add("en")
    assert choose_engine("auto", "openvoice", EN) == ("natural", None)


def test_auto_falls_back_to_the_light_engine(downloaded):
    assert choose_engine("auto", "openvoice", EN) == ("openvoice", None)
    # con GPU el perfil pide XTTS, pero si no está instalado se usa el liviano
    assert choose_engine("auto", "xtts", EN) == ("openvoice", None)


def test_natural_in_a_language_it_does_not_speak_uses_the_light_engine(downloaded):
    engine, note = choose_engine("natural", "openvoice", UK)
    assert engine == "openvoice" and "Ucraniano" in note


def test_natural_requested_but_not_downloaded_explains_how(downloaded):
    with pytest.raises(RuntimeError, match="download-models"):
        choose_engine("natural", "openvoice", EN)


def test_xtts_requested_but_not_installed(downloaded):
    with pytest.raises(RuntimeError, match="coqui-tts"):
        choose_engine("xtts", "openvoice", EN)


def test_languages_of_the_natural_voice():
    assert {code for code in pocket_voice.LANGUAGES} == {"en", "es", "fr", "de", "pt", "it", "nl"}
    assert pocket_voice.supports("es") and not pocket_voice.supports("ru")


def test_checking_availability_does_not_touch_torch_threads():
    torch.set_num_threads(3)
    pocket_voice.cloning_ready("en")  # sin el modelo descargado: False, sin importar pocket_tts
    assert torch.get_num_threads() == 3
