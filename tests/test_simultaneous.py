"""Dónde corta la traducción simultánea, con reconocimientos simulados."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from clonavoz.simultaneous import ClauseSplitter, clauses  # noqa: E402

# Como devuelve Parakeet: pedazos de texto y en qué segundo empieza cada uno.
TOKENS = [" Yo", " así", " lo", " cre", "o", ",", " respond", "ió", " San", "cho", ",", " y", " quer", "ría"]
TIMES = [0.32, 0.72, 1.04, 1.2, 1.36, 1.6, 1.76, 2.1, 2.4, 2.72, 2.96, 3.12, 3.28, 3.52]


def split(seconds, final=False, tokens=TOKENS, times=TIMES):
    audio = np.zeros(int(seconds * 16000), dtype=np.float32)
    n = sum(1 for t in times if t < seconds)
    return ClauseSplitter(lambda _a: (tokens[:n], times[:n]))(audio, final)


def test_cuts_after_the_last_comma_with_the_next_word_already_heard():
    cut, text = split(3.8)
    assert text == "Yo así lo creo, respondió Sancho,"
    assert cut == int((3.12 - 0.04) * 16000)  # justo antes de " y"


def test_does_not_cut_on_a_comma_that_was_just_heard():
    # la segunda coma (2.96 s) está pegada al final: puede cambiar; se corta en la primera
    _, text = split(3.1)
    assert text == "Yo así lo creo,"


def test_does_not_cut_tiny_pieces():
    assert split(1.9, tokens=[" Sí", ",", " claro"], times=[0.3, 0.6, 0.8]) is None


def test_without_punctuation_it_waits_and_then_cuts_between_words():
    tokens = [" y", " quería", " decirte", " que", " mañana", " no", " voy", " a", " poder", " ir"]
    times = [0.2, 0.5, 1.0, 1.5, 1.8, 2.4, 2.7, 3.1, 3.3, 3.9]
    assert split(3.0, tokens=tokens, times=times) is None  # todavía no pasaron 4 s
    cut, text = split(4.5, tokens=tokens, times=times)
    assert text == "y quería decirte que mañana no voy a poder"
    assert cut == int((3.9 - 0.04) * 16000)


def test_in_a_pause_a_finished_sentence_goes_out_right_away():
    tokens = [" Hola", ",", " cómo", " est", "ás", "?"]
    times = [0.2, 0.5, 0.6, 0.9, 1.1, 1.3]
    cut, text = split(1.9, final=True, tokens=tokens, times=times)
    assert (cut, text) == (int(1.9 * 16000), "Hola, cómo estás?")
    assert split(1.9, final=True, tokens=tokens[:5], times=times[:5]) is None  # sin punto: esperar


def test_a_recognition_error_just_means_no_cut():
    def broken(_audio):
        raise RuntimeError("falló")

    assert ClauseSplitter(broken)(np.zeros(16000 * 5, dtype=np.float32), False) is None


def test_the_translation_is_said_in_parts_cut_at_commas():
    assert clauses("Tomorrow I won't be able to go to the meeting, because I have to take my son to the doctor.") == [
        "Tomorrow I won't be able to go to the meeting,",
        "because I have to take my son to the doctor.",
    ]
    assert clauses("Hey, how are you?") == ["Hey, how are you?"]  # no parte pedacitos
    assert clauses("I will check it in time, OK?") == ["I will check it in time, OK?"]
    assert clauses("") == []
