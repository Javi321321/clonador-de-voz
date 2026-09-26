"""Notebooks lentas: lo que se decide con lo que tarda la PC (medido al
arrancar), la voz rápida, la versión int8 de la voz natural y la ventana corta
de Whisper. Sin modelos: todo con piezas falsas."""
import queue
import sys
import threading
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import fake_sounddevice  # noqa: E402

fake_sounddevice.install()

from clonavoz import asr, pipeline, pocket_voice, voice_clone  # noqa: E402
from clonavoz.config import get_profile  # noqa: E402
from clonavoz.languages import get_language  # noqa: E402
from clonavoz.pipeline import SpeedCheck  # noqa: E402

EN = get_language("en")


def test_load_is_understanding_plus_speaking():
    assert SpeedCheck(asr=0.2, voice=0.4).load == pytest.approx(0.6)
    assert SpeedCheck(asr=None, voice=0.4).load == pytest.approx(0.7)  # sin medir: una PC normal
    assert SpeedCheck(asr=0.2, voice=None).load is None


def test_translating_while_speaking_only_if_there_is_cpu_left():
    assert SpeedCheck(asr=0.15, voice=0.4).partials_fit  # PC rápida de 2 núcleos
    assert not SpeedCheck(asr=0.7, voice=0.3).partials_fit  # notebook lenta


class FakeSynth:
    created = []

    def __init__(self, profile, reference_wav, engine="openvoice"):
        self.engine = engine
        FakeSynth.created.append(engine)

    def preload(self, language):
        pass

    def synthesize(self, text, language):
        return np.zeros(24000, dtype=np.float32), 24000

    def can_stream(self, language):
        return False


class FakeVAD:
    def __init__(self, *args, **kwargs):
        self.split_while_speaking = True


class FakeOutput:
    STREAM_CUSHION_SECONDS = 0.25


def _pipeline(monkeypatch, engine="natural", allow=True, simultaneous=False, asr_name="Parakeet"):
    FakeSynth.created = []
    monkeypatch.setattr(pipeline, "VoiceSynthesizer", FakeSynth)
    p = object.__new__(pipeline.LiveVoicePipeline)
    p.profile = get_profile("low")
    p.target_language = EN
    p.source_language = get_language("es")
    p._reference_wav = Path("mi_voz.wav")
    p._allow_engine_fallback = allow
    p._synth = FakeSynth(None, None, engine)
    p._asr = type("FakeASR", (), {"name": asr_name})()
    p.asr_name = asr_name
    p._vad = FakeVAD()
    p.output = FakeOutput()
    p.stream_playback = False
    p.simultaneous = p.translate_while_speaking = simultaneous
    p.fallback_from = None
    p.slow_parakeet = None
    p.messages = []
    p.on_message = p.messages.append
    return p


def test_too_slow_for_the_cloned_voice_uses_the_fast_voice(monkeypatch):
    p = _pipeline(monkeypatch)
    p.speed = SpeedCheck(asr=0.3, voice=1.8)
    monkeypatch.setattr(p, "_measure_voice", lambda text: (0.3, None), raising=False)
    p._adapt_to_speed("Hello.")
    assert p._synth.engine == "rapida" and p.fallback_from == "natural"
    assert p.speed.voice == 0.3
    assert any("voz rápida" in m for m in p.messages)
    assert any("1.8 s" in m for m in p.messages)


def test_without_a_fast_voice_for_that_language_it_keeps_the_cloned_one(monkeypatch):
    p = _pipeline(monkeypatch)
    p.speed = SpeedCheck(asr=0.3, voice=1.8)

    class NoPiper(FakeSynth):
        def preload(self, language):
            raise RuntimeError("no hay voz de Piper para ese idioma")

    monkeypatch.setattr(pipeline, "VoiceSynthesizer", NoPiper)
    p._adapt_to_speed("Hello.")
    assert p._synth.engine == "natural" and p.fallback_from is None


def test_if_you_chose_your_cloned_voice_it_stays_and_explains(monkeypatch):
    p = _pipeline(monkeypatch, allow=False)
    p.speed = SpeedCheck(asr=0.3, voice=1.8)
    p._adapt_to_speed("Hello.")
    assert p._synth.engine == "natural" and p.fallback_from is None
    assert any("atrasando" in m for m in p.messages)
    assert any("--voice-engine rapida" in m for m in p.messages)


def test_a_fast_pc_keeps_the_cloned_voice_and_says_nothing(monkeypatch):
    p = _pipeline(monkeypatch, simultaneous=True)
    p.speed = SpeedCheck(asr=0.15, voice=0.4, rhythm=0.35)
    p._adapt_to_speed("Hello.")
    assert p._synth.engine == "natural" and not p.messages
    assert p.stream_playback and p.output.STREAM_CUSHION_SECONDS == 0.15
    assert p.translate_while_speaking and p._vad.split_while_speaking


def test_without_cpu_left_it_translates_at_each_pause(monkeypatch):
    p = _pipeline(monkeypatch, simultaneous=True)
    p.speed = SpeedCheck(asr=0.35, voice=0.4)
    p._adapt_to_speed("Hello.")
    assert not p.translate_while_speaking and not p._vad.split_while_speaking


def test_a_slow_parakeet_is_replaced_by_whisper(monkeypatch):
    p = _pipeline(monkeypatch, simultaneous=True)
    p.speed = SpeedCheck(asr=0.7, voice=0.3)
    switched = []

    def use_fast_asr():
        switched.append(True)
        p.asr_name = "Whisper tiny"
        p.simultaneous = p.translate_while_speaking = False

    monkeypatch.setattr(p, "_use_fast_asr", use_fast_asr, raising=False)
    monkeypatch.setattr(p, "_measure_asr", lambda: 0.25, raising=False)
    p._adapt_to_speed("Hello.")
    assert switched and p.speed.asr == 0.25
    assert any("Parakeet" in m and "Whisper tiny" in m for m in p.messages)


# --- la voz rápida ---


class FakePiper:
    def __init__(self, speaker_pitch_hz=None):
        self.pitch = speaker_pitch_hz

    def preload(self, code):
        pass

    def synthesize(self, text, code):
        return np.full(22050, 0.1, dtype=np.float32), 22050


def test_the_fast_voice_is_piper_alone(monkeypatch, tmp_path):
    import soundfile as sf

    sample = tmp_path / "mi_voz.wav"
    sf.write(str(sample), np.zeros(16000, dtype=np.float32), 16000)
    monkeypatch.setattr(voice_clone, "PiperSynthesizer", FakePiper)
    monkeypatch.setattr(voice_clone, "median_pitch", lambda audio, rate: 110)
    converter = []
    monkeypatch.setattr(voice_clone.openvoice.ToneColorConverter, "from_pretrained", lambda: converter.append(1))
    synth = voice_clone.VoiceSynthesizer(get_profile("low"), sample, engine="rapida")
    synth.preload(EN)
    audio, rate = synth.synthesize("Hello.", EN)
    assert rate == 22050 and len(audio) == 22050
    assert not converter  # no carga el conversor de timbre
    assert voice_clone.choose_engine("rapida", "openvoice", EN) == ("rapida", None)


# --- voz natural int8 ---


def test_int8_needs_avx2(monkeypatch):
    import torch

    monkeypatch.setattr(pocket_voice.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(torch.backends.cpu, "get_cpu_capability", lambda: "AVX2")
    assert pocket_voice.int8_supported()
    monkeypatch.setattr(torch.backends.cpu, "get_cpu_capability", lambda: "DEFAULT")  # Celeron/Pentium sin AVX2
    assert not pocket_voice.int8_supported()


def test_if_int8_does_not_load_it_uses_the_normal_voice(monkeypatch, tmp_path):
    loads = []

    class FakeModel:
        sample_rate = 24000

        @staticmethod
        def load_model(config, quantize=False):
            loads.append(quantize)
            if quantize:
                raise RuntimeError("sin int8")
            return FakeModel()

    monkeypatch.setattr(pocket_voice, "_import_pocket", lambda: (FakeModel, None))
    monkeypatch.setattr(pocket_voice, "_config_path", lambda code: tmp_path / "english.yaml")
    voice = pocket_voice.PocketVoice(tmp_path / "mi_voz.wav", int8=True)
    voice._model("en")
    assert loads == [True, False] and voice.int8 is False


def test_loading_int8_shows_no_warnings(monkeypatch):
    import warnings

    class FakeModel:
        @staticmethod
        def load_model(quantize=False, **kwargs):
            warnings.warn("torch.quantize_per_tensor ... deprecated", UserWarning, stacklevel=1)
            warnings.warn("torch.ao.quantization is deprecated", DeprecationWarning, stacklevel=1)
            return FakeModel()

    monkeypatch.setattr(pocket_voice, "_import_pocket", lambda: (FakeModel, None))
    with warnings.catch_warnings(record=True) as shown:
        warnings.simplefilter("always")
        pocket_voice.load_model(True, language="english")
    assert not shown


# --- Whisper con la ventana corta ---


def test_repeated_text_has_a_high_compression_ratio():
    assert asr._compression_ratio("Hola, ¿cómo estás?") < 2.4
    assert asr._compression_ratio("Hola, ¿cómo estás? " * 12) > 2.4


def test_long_phrases_use_the_normal_window():
    class FakeExtractor:
        def __call__(self, audio):
            return np.zeros((80, len(audio) // 160 + 1), dtype=np.float32)

    recognizer = object.__new__(asr.SpeechRecognizer)
    recognizer._model = type("FakeWhisper", (), {"feature_extractor": FakeExtractor()})()
    assert recognizer._transcribe_short(np.zeros(16000 * 8, dtype=np.float32), "es") is None


def test_the_language_of_long_phrases_comes_from_their_first_seconds():
    class FakeExtractor:  # como el de faster-whisper: un cuadro cada 10 ms, más uno
        def __call__(self, audio):
            return np.zeros((80, len(audio) // 160 + 1), dtype=np.float32)

    class FakeCT2:
        @staticmethod
        def detect_language(encoded):
            return [[("<|pt|>", 0.9), ("<|es|>", 0.1)]]

    encoded = []
    model = type("FakeWhisper", (), {"feature_extractor": FakeExtractor(), "model": FakeCT2()})()
    model.encode = lambda window: encoded.append(window.shape) or "codificado"
    recognizer = object.__new__(asr.SpeechRecognizer)
    recognizer._model = model
    for seconds in (2.0, 7.0, 12.0):
        probabilities, reusable = recognizer.language_probabilities(np.zeros(int(16000 * seconds), dtype=np.float32))
        assert probabilities == {"pt": 0.9, "es": 0.1}
        assert (reusable is not None) == (seconds < 7.0)  # lo codificado sirve para el texto solo si entró entera
    assert all(shape == (80, 1000) for shape in encoded)


# --- el VAD sin reconocer mientras hablás ---


def test_the_vad_can_skip_recognizing_while_you_speak():
    from clonavoz.vad import StreamingVAD

    calls = []

    def splitter(audio, final):
        calls.append(final)
        return None

    vad = StreamingVAD(max_utterance_seconds=5.0, model=lambda frame, rate: 0.9, splitter=splitter)
    vad.split_while_speaking = False
    for _ in range(200):  # 6.4 s hablando de corrido
        vad.push(np.zeros(512, dtype=np.float32))
    assert calls == []


def test_catch_up_queue_still_works_without_models():
    # que el pipeline armado a mano siga sirviendo para las otras pruebas
    p = object.__new__(pipeline.LiveVoicePipeline)
    p._audio_out_queue = queue.Queue()
    p._backlog = 0.0
    p._backlog_lock = threading.Lock()
    assert p._catch_up_speed(0.2) == 1.0
