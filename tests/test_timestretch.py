"""Hablar más rápido sin cambiar el tono, y acortar pausas (para alcanzarte
cuando la traducción se atrasa)."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from clonavoz.timestretch import shorten_pauses, speed_up  # noqa: E402

RATE = 24000


def _tone(seconds, freq=220.0):
    t = np.arange(int(seconds * RATE)) / RATE
    return (0.5 * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def _main_frequency(audio):
    spectrum = np.abs(np.fft.rfft(audio * np.hanning(len(audio))))
    return np.argmax(spectrum) * RATE / len(audio)


def test_faster_is_shorter_with_the_same_pitch():
    audio = _tone(2.0)
    faster = speed_up(audio, RATE, 1.3)
    assert abs(len(faster) / len(audio) - 1 / 1.3) < 0.03
    assert abs(_main_frequency(faster) - 220.0) < 3
    assert np.max(np.abs(faster)) < 0.6  # sin picos al empalmar


def test_normal_speed_or_tiny_audio_is_left_as_is():
    audio = _tone(2.0)
    np.testing.assert_array_equal(speed_up(audio, RATE, 1.0), audio)
    tiny = _tone(0.05)
    np.testing.assert_array_equal(speed_up(tiny, RATE, 1.3), tiny)


def test_long_pauses_in_the_middle_are_shortened():
    silence = np.zeros(int(0.8 * RATE), dtype=np.float32)
    edge = np.zeros(int(0.5 * RATE), dtype=np.float32)
    audio = np.concatenate([edge, _tone(0.5), silence, _tone(0.5), edge])
    shorter = shorten_pauses(audio, RATE, longer_than=0.25, keep=0.12)
    # la pausa del medio queda en 0.12 s; las del principio y el final, igual
    assert abs((len(audio) - len(shorter)) / RATE - (0.8 - 0.12)) < 0.02


def test_short_pauses_are_kept():
    pause = np.zeros(int(0.2 * RATE), dtype=np.float32)
    audio = np.concatenate([_tone(0.5), pause, _tone(0.5)])
    assert len(shorten_pauses(audio, RATE)) == len(audio)
