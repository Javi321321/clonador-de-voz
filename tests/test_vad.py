"""Pruebas del corte de frases: con un detector de voz falso que devuelve
probabilidades guionadas, sin modelo ni audio real."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from clonavoz.vad import StreamingVAD  # noqa: E402

FRAME = StreamingVAD.frame_size()
SPEECH, QUIET = 0.9, 0.05


class ScriptedModel:
    def __init__(self, probabilities):
        self._probs = iter(probabilities)
        self.resets = 0

    def __call__(self, _audio, _rate):
        return next(self._probs)

    def reset_states(self):
        self.resets += 1


def run(probabilities, max_utterance_seconds=3.0):
    """Frases liberadas, en cantidad de bloques de 32 ms."""
    model = ScriptedModel(probabilities)
    vad = StreamingVAD(max_utterance_seconds=max_utterance_seconds, model=model)
    lengths = []
    for _ in probabilities:
        utterance = vad.push(np.zeros(FRAME, dtype=np.float32))
        if utterance is not None:
            lengths.append(len(utterance) // FRAME)
    return lengths, vad, model


def test_a_pause_ends_the_phrase_and_the_trailing_silence_is_trimmed():
    lengths, vad, model = run([QUIET] * 5 + [SPEECH] * 40 + [QUIET] * 20)
    # 1 bloque de antes + 40 de voz + 3 de silencio (el resto se descarta)
    assert lengths == [44]
    assert not vad.is_speaking
    assert model.resets == 1


def test_short_pauses_do_not_cut_a_short_phrase():
    lengths, _, _ = run([SPEECH] * 50 + [QUIET] * 5 + [SPEECH] * 20 + [QUIET] * 20)
    assert len(lengths) == 1


def test_a_long_phrase_is_cut_at_a_short_pause_not_mid_word():
    # 3 s = 94 bloques. La primera pausa corta (a 1.6 s) no corta; la que
    # viene después de los 3 s sí, y el resto sale como otra frase.
    probs = [SPEECH] * 50 + [QUIET] * 5 + [SPEECH] * 60 + [QUIET] * 5 + [SPEECH] * 20 + [QUIET] * 20
    lengths, _, _ = run(probs)
    assert len(lengths) == 2
    assert lengths[0] == 50 + 5 + 60 + 3  # cortada en la segunda pausa (deja 3 bloques de silencio)
    assert 20 <= lengths[1] <= 25


def test_without_any_pause_it_still_cuts_eventually():
    lengths, vad, _ = run([SPEECH] * 400)
    hard_max_frames = int(np.ceil(3.0 * 2.5 * 16000 / FRAME))
    assert lengths[0] == hard_max_frames
    assert vad.is_speaking  # y sigue con la frase siguiente
