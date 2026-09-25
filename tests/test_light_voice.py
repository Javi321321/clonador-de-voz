"""Pruebas del motor de voz liviano: el conversor de timbre OpenVoice (con
pesos al azar, así no hace falta descargar el modelo), la estimación del tono
de voz y la elección de la voz base de Piper para cada idioma.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pytest
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from clonavoz import openvoice, piper_tts  # noqa: E402
from clonavoz.languages import list_languages  # noqa: E402
from clonavoz.openvoice import ToneColorConverter, _fold_weight_norm  # noqa: E402
from clonavoz.piper_tts import median_pitch, resolve_voice_id  # noqa: E402


@pytest.fixture(scope="module")
def converter():
    torch.manual_seed(0)
    return ToneColorConverter()


def _voice(pitch_hz, rate=16000, seconds=2.0):
    """Señal periódica con armónicos, como una vocal sostenida."""
    t = np.arange(int(rate * seconds)) / rate
    return sum(np.sin(2 * np.pi * pitch_hz * k * t) / k for k in range(1, 6)).astype(np.float32) * 0.2


def test_embedding_shape(converter):
    assert tuple(converter.embedding(_voice(120, 22050), 22050).shape) == (1, 256, 1)


def test_convert_keeps_duration_and_resamples(converter):
    source = converter.embedding(_voice(120, 22050), 22050)
    target = converter.embedding(_voice(210, 22050), 22050)
    out = converter.convert(_voice(150, 24000, 1.0), 24000, source, target)  # XTTS/Piper a 24 kHz
    assert abs(len(out) - openvoice.SAMPLE_RATE) < 512
    assert np.isfinite(out).all() and np.abs(out).max() <= 1.0


def test_convert_too_short_audio_does_not_crash(converter):
    source = converter.embedding(_voice(120, 22050), 22050)
    assert len(converter.convert(np.zeros(100, np.float32), 22050, source, source)) == 100


@pytest.mark.filterwarnings("ignore::FutureWarning")  # el checkpoint se guardó con esa API vieja
def test_fold_weight_norm_matches_torch():
    conv = torch.nn.utils.weight_norm(torch.nn.Conv1d(4, 6, 3))
    state = {f"c.{k}": v for k, v in conv.state_dict().items()}
    folded = _fold_weight_norm(state)
    assert set(folded) == {"c.weight", "c.bias"}
    assert torch.allclose(folded["c.weight"], conv.weight, atol=1e-6)


@pytest.mark.parametrize("pitch", [95, 120, 180, 220])
def test_median_pitch(pitch):
    assert median_pitch(_voice(pitch), 16000) == pytest.approx(pitch, rel=0.05)


def test_median_pitch_of_silence_is_none():
    assert median_pitch(np.zeros(32000, np.float32), 16000) is None


def test_base_voice_follows_speaker_pitch():
    assert resolve_voice_id("en", 110) == "en_US-joe-medium"  # voz grave
    assert resolve_voice_id("en", 210) == "en_US-ljspeech-medium"  # voz aguda
    assert resolve_voice_id("en", None) == "en_US-joe-medium"
    assert resolve_voice_id("de", 210) == "de_DE-thorsten-medium"  # única voz del idioma


def test_user_override_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("CLONAVOZ_HOME", str(tmp_path))  # como la versión portable
    (tmp_path / "piper_voices.json").write_text(json.dumps({"en": "en_GB-alba-medium"}))
    assert resolve_voice_id("en", 110) == "en_GB-alba-medium"


def test_every_language_has_a_base_voice_and_calibration():
    without_voice = set()
    for lang in list_languages():
        try:
            resolve_voice_id(lang.code)
        except RuntimeError:
            without_voice.add(lang.code)
            continue
        assert lang.code in piper_tts._CALIBRATION
    # Idiomas sin ninguna voz en el catálogo de Piper (se pueden agregar a mano).
    assert without_voice == {"hr", "gl", "ms"}


def test_library_telemetry_is_disabled():
    import os

    import clonavoz  # noqa: F401

    assert os.environ["ORT_DISABLE_TELEMETRY"] == "1"
    assert os.environ["HF_HUB_DISABLE_TELEMETRY"] == "1"
