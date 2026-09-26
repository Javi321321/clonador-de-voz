"""Cómo suena la traducción que llega por pedacitos: a medida que se genera,
y un poco más rápida mientras viene atrasada. Sin modelos: se arma el
pipeline a mano con una salida falsa."""
import queue
import sys
import threading
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import fake_sounddevice  # noqa: E402

fake_sounddevice.install()

from clonavoz import pipeline  # noqa: E402

RATE = 24000


class FakeOutput:
    def __init__(self):
        self.streamed = []  # cada frase, tal como sonó

    def play_stream(self, chunks, rate):
        self.streamed.append(np.concatenate([np.asarray(c, dtype=np.float32) for c in chunks]))


def _pipeline(backlog):
    p = object.__new__(pipeline.LiveVoicePipeline)
    p.output = FakeOutput()
    p._audio_out_queue = queue.Queue()
    p._backlog = backlog
    p._backlog_lock = threading.Lock()
    return p


def _queue_phrase(p, seconds):
    chunk = (0.3 * np.sin(2 * np.pi * 200 * np.arange(int(0.08 * RATE)) / RATE)).astype(np.float32)
    for _ in range(int(seconds / 0.08)):
        p._audio_out_queue.put(("chunk", chunk))
    p._audio_out_queue.put(("stream_end",))


def test_on_time_it_sounds_as_it_arrives():
    p = _pipeline(backlog=0.5)
    _queue_phrase(p, 2.0)
    p._play_phrase_stream(RATE)
    assert len(p.output.streamed[0]) == int(2.0 / 0.08) * int(0.08 * RATE)
    assert p._backlog == 0.0


def test_behind_it_is_said_faster_while_it_arrives():
    p = _pipeline(backlog=10.0)  # sigue atrasada toda la frase
    _queue_phrase(p, 2.0)
    p._play_phrase_stream(RATE)
    length = len(p.output.streamed[0]) / RATE
    assert abs(length - 2.0 / 1.3) < 0.05


def test_behind_the_pauses_are_shortened_even_more():
    p = _pipeline(backlog=10.0)
    _queue_phrase(p, 1.0)
    p._audio_out_queue.queue.pop()  # sin el fin de frase: se agrega una pausa
    for _ in range(10):
        p._audio_out_queue.put(("chunk", np.zeros(int(0.08 * RATE), dtype=np.float32)))
    p._audio_out_queue.put(("stream_end",))
    p._play_phrase_stream(RATE)
    length = len(p.output.streamed[0]) / RATE
    assert abs(length - (1.0 / 1.3 + 0.8 / 2.6)) < 0.06


def test_once_it_catches_up_the_rest_goes_at_normal_speed():
    p = _pipeline(backlog=1.2)  # a los ~0.7 s ya no está atrasada
    _queue_phrase(p, 2.0)
    p._play_phrase_stream(RATE)
    audio = p.output.streamed[0]
    assert 2.0 - 0.15 < len(audio) / RATE < 2.0 - 0.02
    assert np.max(np.abs(np.diff(audio))) < 0.05  # sin saltos al volver a la normal


def test_catch_up_speed():
    p = _pipeline(backlog=0.0)
    assert p._catch_up_speed(0.4) == 1.0
    assert p._catch_up_speed(1.0) == 1.1
    assert p._catch_up_speed(2.0) == 1.2
    assert p._catch_up_speed(5.0) == 1.3
