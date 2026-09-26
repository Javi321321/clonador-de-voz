"""Reconocimiento en streaming (Vosk): el detector de voz le pasa el audio
mientras hablás y cada frase sale con su texto; la puntuación que Vosk no pone;
y cuándo se elige Vosk. Sin modelos: con piezas falsas."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from clonavoz import asr, parakeet_asr, vosk_asr  # noqa: E402
from clonavoz.config import get_profile  # noqa: E402
from clonavoz.punctuation import is_question, restore  # noqa: E402
from clonavoz.vad import StreamingVAD  # noqa: E402

FRAME = StreamingVAD.frame_size()
SPEECH, QUIET = 0.9, 0.05


class FakeStream:
    def __init__(self):
        self.fed = 0
        self.texts = ["hola cómo estás", "bien gracias"]

    def accept(self, audio):
        self.fed += len(audio)

    def text(self):
        return self.texts.pop(0)


def _vad(probabilities, stream, **kwargs):
    probs = iter(probabilities)
    vad = StreamingVAD(max_utterance_seconds=3.0, model=lambda frame, rate: next(probs), stream=stream, **kwargs)
    utterances = [vad.push(np.zeros(FRAME, dtype=np.float32)) for _ in probabilities]
    return [u for u in utterances if u is not None]


def test_each_phrase_comes_out_with_the_text_recognized_while_speaking():
    stream = FakeStream()
    utterances = _vad([QUIET] * 3 + [SPEECH] * 30 + [QUIET] * 20 + [SPEECH] * 20 + [QUIET] * 20, stream)
    assert [u.text for u in utterances] == ["hola cómo estás", "bien gracias"]
    # se le pasó lo que se dijo (y un poco de antes), no los silencios de entre frases
    assert FRAME * 50 <= stream.fed <= FRAME * 95


def test_with_streaming_a_shorter_pause_ends_the_phrase():
    # 350 ms = 11 bloques de silencio: con 12 ya terminó la frase
    utterances = _vad([SPEECH] * 30 + [QUIET] * 12, FakeStream(), end_silence_ms=350)
    assert len(utterances) == 1
    assert _vad([SPEECH] * 30 + [QUIET] * 12, FakeStream()) == []  # con los 500 ms de siempre, todavía no


def test_questions_are_recognized_by_how_they_start():
    assert is_question("me podés repetir la última parte", "es")
    assert is_question("me puede repetir la última parte", "es")
    assert is_question("hola cómo estás", "es")
    assert is_question("y vos qué hacés", "es")
    assert is_question("por qué no viniste", "es")
    assert is_question("can you hear me", "en")
    assert not is_question("creo que sí", "es")
    assert not is_question("mañana no voy a poder ir", "es")
    assert not is_question("tenés razón", "es")  # puede ser afirmación: no se marca


def test_restore_adds_capital_and_punctuation():
    assert restore("me podés repetir la última parte", "es") == "¿Me podés repetir la última parte?"
    assert restore("muchas gracias nos hablamos", "es") == "Muchas gracias nos hablamos."
    assert restore("can you hear me", "en") == "Can you hear me?"
    assert restore("  ", "es") == ""


def test_vosk_is_used_when_parakeet_is_not(monkeypatch):
    class FakeVosk:
        def __init__(self, code):
            self.code = code

        def transcribe(self, audio):
            return "hola"

        def stream(self):
            return FakeStream()

    monkeypatch.setattr(parakeet_asr, "usable", lambda code: False)
    monkeypatch.setattr(vosk_asr, "ready", lambda code: code == "es")
    monkeypatch.setattr(vosk_asr, "VoskModel", FakeVosk)
    recognizer = asr.SpeechRecognizer(get_profile("low"), "es")
    assert recognizer.name == "Vosk" and recognizer.streaming and not recognizer.can_split
    assert recognizer.transcribe(np.zeros(16000, dtype=np.float32), "es") == "hola"


def test_without_parakeet_on_purpose_vosk_is_used(monkeypatch):
    monkeypatch.setattr(parakeet_asr, "usable", lambda code: True)
    monkeypatch.setattr(vosk_asr, "ready", lambda code: True)
    monkeypatch.setattr(vosk_asr, "VoskModel", lambda code: object())
    assert asr.SpeechRecognizer(get_profile("low"), "es", parakeet=False).name == "Vosk"


def test_vosk_languages_and_models():
    assert {"es", "en", "fr", "de", "it"} <= set(vosk_asr.MODELS)
    assert not vosk_asr.supports("pt")  # su modelo chico se equivoca mucho
    for name, md5 in vosk_asr.MODELS.values():
        assert name.startswith("vosk-model-small-") and len(md5) == 32
