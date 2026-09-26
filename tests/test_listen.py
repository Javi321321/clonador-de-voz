"""Lo que te dicen, traducido para vos (sin modelos: piezas falsas)."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import fake_sounddevice  # noqa: E402

fake_sounddevice.install()

from clonavoz import listen  # noqa: E402
from clonavoz.language_id import LanguageTracker  # noqa: E402
from clonavoz.languages import get_language  # noqa: E402
from clonavoz.vad import Utterance  # noqa: E402

ES = get_language("es")


class FakeWhisper:
    def __init__(self, probabilities):
        self.probabilities = probabilities
        self.transcribed = []

    def language_probabilities(self, audio):
        return self.probabilities, "codificado"

    def transcribe_encoded(self, audio, language, encoded=None):
        self.transcribed.append(language)
        return {"en": "Hi there, how are you?", "pt": "Oi, tudo bem?", "ja": "こんにちは"}.get(language, "")


class FakeParakeet:
    def __init__(self, text):
        self.text = text

    def transcribe(self, audio):
        return self.text


def recognizer(probabilities, parakeet_text=None):
    rec = object.__new__(listen.CallRecognizer)
    rec._whisper = FakeWhisper(probabilities)
    rec._parakeet = FakeParakeet(parakeet_text) if parakeet_text is not None else None
    rec.tracker = LanguageTracker(priority=("en", "pt", "es"))
    return rec


SECOND = np.zeros(16000 * 2, dtype=np.float32)


def test_parakeet_understands_and_whisper_only_names_the_language():
    rec = recognizer({"pt": 0.8, "es": 0.2}, parakeet_text="Oi, tudo bem?")
    assert rec.recognize(SECOND) == ("pt", "Oi, tudo bem?")
    assert rec._whisper.transcribed == []  # el texto salió de Parakeet


def test_languages_parakeet_does_not_know_go_to_whisper():
    rec = recognizer({"ja": 0.97, "en": 0.01}, parakeet_text="konnichiwa")
    assert rec.recognize(SECOND) == ("ja", "こんにちは")


def test_without_parakeet_a_possible_switch_is_confirmed_by_the_words():
    rec = recognizer({"en": 0.9, "pt": 0.05})
    for _ in range(3):
        assert rec.recognize(SECOND)[0] == "en"
    rec._whisper.probabilities = {"en": 0.3, "pt": 0.6}
    assert rec.recognize(SECOND) == ("pt", "Oi, tudo bem?")
    assert rec._whisper.transcribed[-1] == "pt"


def test_noise_does_not_count_for_the_language():
    rec = recognizer({"en": 0.9}, parakeet_text="Hi there.")
    rec.recognize(SECOND)
    rec._parakeet.text = ""
    rec._whisper.probabilities = {"pt": 0.99}
    code, text = rec.recognize(SECOND)
    assert text == "" and rec.tracker.current == "en"


# --- la voz con que escuchás ---


class FakePiper:
    def __init__(self, speaker_pitch_hz=None, voices=None):
        self.voices = voices

    def preload(self, code):
        pass

    def synthesize(self, text, code):
        return np.full(100, 0.1, dtype=np.float32), 22050


def test_their_voice_is_a_man_or_a_woman_by_their_pitch(monkeypatch):
    monkeypatch.setattr(listen, "PiperSynthesizer", FakePiper)
    voice = listen.TheirVoice("parecida", ES)
    monkeypatch.setattr(listen, "median_pitch", lambda audio, rate: 210.0)
    voice.listen(SECOND)
    assert voice._piper[voice._high].voices == {"es": "es_MX-claude-high"}
    monkeypatch.setattr(listen, "median_pitch", lambda audio, rate: 110.0)
    voice.listen(SECOND)
    assert voice._piper[voice._high].voices == {"es": "es_ES-davefx-medium"}
    assert voice._heard == []  # "parecida": su voz no se guarda ni se clona


def test_their_voice_is_cloned_only_when_allowed(monkeypatch):
    monkeypatch.setattr(listen, "PiperSynthesizer", FakePiper)
    monkeypatch.setattr(listen, "median_pitch", lambda audio, rate: 120.0)
    made = []

    class FakeClone:
        def __init__(self, audio, rate, piper, language):
            made.append(len(audio) / rate)

        def synthesize(self, text, code):
            return np.zeros(10, dtype=np.float32), 22050

    monkeypatch.setattr(listen, "_OpenVoiceClone", FakeClone)
    monkeypatch.setattr(listen.pocket_voice, "cloning_ready", lambda code: False)
    class NowThread:  # clona en el momento, sin otro hilo
        def __init__(self, target, **kwargs):
            self.start = target

    monkeypatch.setattr(listen.threading, "Thread", NowThread)
    voice = listen.TheirVoice("clonada", ES)
    for _ in range(4):
        voice.listen(SECOND)  # 8 s de su voz
    assert made == [8.0] and voice._clone is not None
    assert voice._heard == []  # no se guarda lo que dijo
    assert any("su voz clonada" in m for m in voice.messages)


# --- el pipeline ---


class FakeRecognizer:
    name = "falso"

    def __init__(self, results):
        self.results = list(results)

    def recognize(self, audio):
        return self.results.pop(0)


class FakeTranslator:
    name = "Opus-MT (falso)"

    def translate(self, text):
        return f"[es] {text}"


def make_pipeline(results, monkeypatch, excludes_own_audio=True):
    p = object.__new__(listen.IncomingPipeline)
    p.target_language = ES
    p.recognizer = FakeRecognizer(results)
    p._translators = {}
    monkeypatch.setattr(p, "_translator", lambda code: FakeTranslator(), raising=False)
    p.transcripts, p.spoken, p.languages = [], [], []
    p.on_transcript = lambda code, text, translated: p.transcripts.append((code, text, translated))
    p.on_language = p.languages.append
    p.on_message = print
    p.language = None
    p.stats = listen.PipelineStats()
    p._synth = type("V", (), {"mode": "parecida", "messages": [], "listen": lambda self, a: None})()
    monkeypatch.setattr(p, "_speak", lambda text, language=None: p.spoken.append(text), raising=False)
    p.mic = type("M", (), {"excludes_own_audio": excludes_own_audio})()
    p.muted_while = None
    return p


def test_what_they_say_is_shown_and_spoken_translated(monkeypatch):
    p = make_pipeline([("en", "Hello!")], monkeypatch)
    p._process(Utterance(SECOND))
    assert p.transcripts == [("en", "Hello!", "[es] Hello!")]
    assert p.spoken == ["[es] Hello!"]
    assert p.languages == ["en"]


def test_if_they_speak_your_language_it_is_only_shown(monkeypatch):
    p = make_pipeline([("es", "¡Hola!")], monkeypatch)
    p._process(Utterance(SECOND))
    assert p.transcripts == [("es", "¡Hola!", None)]
    assert p.spoken == []


def test_it_stops_listening_while_its_own_translation_sounds(monkeypatch):
    frame = np.ones(512, dtype=np.float32)
    p = make_pipeline([], monkeypatch, excludes_own_audio=False)
    monkeypatch.setattr(listen.IncomingPipeline, "playing", property(lambda self: True))
    assert not p._filter_frame(frame).any()
    p = make_pipeline([], monkeypatch, excludes_own_audio=True)  # Windows 10 2004+: no hace falta
    assert p._filter_frame(frame).all()
