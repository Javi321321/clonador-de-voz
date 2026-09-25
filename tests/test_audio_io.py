"""Pruebas de la captura/salida de audio y del diagnóstico del micrófono.
Usan un `sounddevice` falso (no necesitan PortAudio ni hardware), pero sí
numpy y scipy, que son dependencias del proyecto.
"""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import fake_sounddevice  # noqa: E402

fake_sounddevice.install()

from clonavoz.audio_io import (  # noqa: E402
    AudioDeviceError,
    AudioOutput,
    MicrophoneStream,
    MicStats,
    StreamResampler,
    beep_signal,
    diagnose_microphone,
)
from clonavoz.console import StatusLine, format_meter  # noqa: E402


def _tone(freq, rate, seconds):
    return np.sin(2 * np.pi * freq * np.arange(int(rate * seconds)) / rate).astype(np.float32)


def _run_in_blocks(resampler, signal, sizes):
    out, pos, i = [], 0, 0
    while pos < len(signal):
        size = sizes[i % len(sizes)]
        out.append(resampler.process(signal[pos : pos + size]))
        pos, i = pos + size, i + 1
    return np.concatenate(out)


def _dominant_freq(signal, rate):
    spectrum = np.abs(np.fft.rfft(signal * np.hanning(len(signal))))
    return np.fft.rfftfreq(len(signal), 1 / rate)[np.argmax(spectrum)]


@pytest.mark.parametrize("from_rate", [48000, 44100, 8000])
def test_resampler_keeps_length_and_pitch(from_rate):
    signal = _tone(1000, from_rate, 2.0)
    out = _run_in_blocks(StreamResampler(from_rate, 16000), signal, [1536, 1000, 7, 3001])
    assert abs(len(out) - 32000) <= 2
    assert abs(_dominant_freq(out[1600:], 16000) - 1000) < 5
    assert np.max(np.abs(out[1600:])) == pytest.approx(1.0, abs=0.05)


def test_resampler_result_does_not_depend_on_block_size():
    signal = _tone(440, 44100, 1.0) * 0.5
    a = _run_in_blocks(StreamResampler(44100, 16000), signal, [1411])
    b = _run_in_blocks(StreamResampler(44100, 16000), signal, [100, 5000, 33])
    assert len(a) == len(b)
    assert np.allclose(a, b, atol=1e-6)


def test_resampler_filters_what_does_not_fit_in_16k():
    # Un tono de 12 kHz no existe a 16 kHz (Nyquist 8 kHz): sin filtro
    # aparecería "espejado" a 4 kHz y confundiría al VAD/ASR.
    out = StreamResampler(48000, 16000).process(_tone(12000, 48000, 1.0))
    assert np.sqrt(np.mean(out[1600:] ** 2)) < 0.01


def test_microphone_uses_16k_directly_when_the_device_accepts_it():
    fake_sounddevice.windows_with_vb_cable()
    frames = []
    mic = MicrophoneStream(2, on_frame=frames.append)
    mic.start()
    stream = fake_sounddevice.streams[-1]
    assert (mic.samplerate, mic.channels, stream.blocksize) == (16000, 1, 512)
    stream.feed(np.full(512, 0.25))
    assert len(frames) == 1 and np.allclose(frames[0], 0.25)


def test_microphone_falls_back_to_native_rate_like_wasapi():
    # Micrófono WASAPI que solo abre a 48 kHz: antes el hilo de captura moría
    # con "Invalid sample rate" y la consola seguía diciendo "Escuchando...".
    fake_sounddevice.windows_with_vb_cable()
    frames = []
    mic = MicrophoneStream(11, on_frame=frames.append)
    mic.start()
    stream = fake_sounddevice.streams[-1]
    assert (mic.samplerate, stream.samplerate, stream.blocksize) == (48000, 48000, 1536)
    assert "remuestreado" in mic.description
    signal = _tone(1000, 48000, 0.96)  # 30 bloques de 1536
    for block in np.split(signal, 30):
        stream.feed(block)
    assert len(frames) == 30 and all(len(f) == 512 for f in frames)
    assert abs(_dominant_freq(np.concatenate(frames)[1600:], 16000) - 1000) < 20


def test_microphone_falls_back_to_stereo():
    d = fake_sounddevice.device
    fake_sounddevice.configure([d("Mic USB", inputs=2, rate=48000, rates=[48000], channels=[2])], default_input=0)
    frames = []
    mic = MicrophoneStream(None, on_frame=frames.append)
    mic.start()
    assert (mic.samplerate, mic.channels) == (48000, 2)
    fake_sounddevice.streams[-1].feed(np.full(1536, 0.5))
    assert len(frames) == 1


def test_microphone_error_explains_what_to_do():
    d = fake_sounddevice.device
    fake_sounddevice.configure([d("Mic roto", inputs=1, rates=[])], default_input=0)
    with pytest.raises(AudioDeviceError) as err:
        MicrophoneStream(0).start()
    assert "Invalid sample rate" in str(err.value) and "--input-device" in str(err.value)


def test_microphone_without_default_device_raises_clear_error():
    fake_sounddevice.configure([], default_input=None)
    with pytest.raises(AudioDeviceError, match="No se encontró el micrófono"):
        MicrophoneStream(None).start()


def test_microphone_level_and_stats():
    fake_sounddevice.windows_with_vb_cable()
    mic = MicrophoneStream(2)
    mic.start()
    stream = fake_sounddevice.streams[-1]
    stream.feed(np.zeros(512))
    assert mic.stats.blocks == 1 and mic.stats.nonzero_blocks == 0
    stream.feed(np.full(512, 0.1))
    assert mic.pop_level_db() == pytest.approx(-20, abs=0.1)
    assert mic.pop_level_db() < -100  # se reinicia en cada lectura
    assert mic.stats.nonzero_blocks == 1


def test_output_opens_at_native_rate_and_falls_back_to_stereo():
    d = fake_sounddevice.device
    fake_sounddevice.configure([d("CABLE Input", outputs=2, rate=48000, rates=[48000], channels=[2])])
    out = AudioOutput(0)
    out.start()
    assert (out.samplerate, out.channels) == (48000, 2)
    out.play(np.ones(24000, dtype=np.float32), 24000)  # 1 s a 24 kHz (XTTS)
    written = fake_sounddevice.streams[-1].written[0]
    assert written.shape == (48000, 2)


def test_diagnose_no_audio_at_all():
    assert "no está entregando audio" in diagnose_microphone(MicStats())[0]


def test_diagnose_digital_silence_points_to_windows_privacy():
    problems = diagnose_microphone(MicStats(blocks=100), windows=True)
    assert "silencio absoluto" in problems[0] and "Privacidad" in problems[1]


def test_diagnose_low_level_only_when_voice_is_expected():
    stats = MicStats(blocks=100, nonzero_blocks=100, max_peak=0.001)
    assert "muy bajo" in diagnose_microphone(stats)[0]
    assert diagnose_microphone(stats, expect_voice=False) == []


def test_diagnose_sound_without_speech_and_all_good():
    stats = MicStats(blocks=100, nonzero_blocks=100, max_peak=0.5)
    assert "no se detectó voz" in diagnose_microphone(stats, heard_speech=False)[0]
    assert diagnose_microphone(stats, heard_speech=True) == []


def test_beep_signal():
    beeps = beep_signal(48000)
    assert len(beeps) == 3 * int(0.75 * 48000)
    assert np.max(np.abs(beeps)) <= 0.3


def test_format_meter():
    assert format_meter(-80, width=10) == "[----------]  -60 dB"
    assert format_meter(-30, width=10) == "[#####-----]  -30 dB"
    assert format_meter(0, width=10) == "[##########]    0 dB"


class _FakeTerminal:
    def __init__(self):
        self.data = ""

    def isatty(self):
        return True

    def write(self, text):
        self.data += text

    def flush(self):
        pass


def test_status_line_prints_messages_above_the_meter():
    term = _FakeTerminal()
    status = StatusLine(term)
    status.update("Mic [###-]")
    status.print("es > hola")
    assert term.data == "\rMic [###-]" + "\r" + " " * 10 + "\r" + "es > hola\n" + "Mic [###-]"


def test_status_line_is_silent_when_not_a_terminal():
    class Pipe(_FakeTerminal):
        def isatty(self):
            return False

    pipe = Pipe()
    status = StatusLine(pipe)
    status.update("Mic [###-]")
    status.print("es > hola")
    assert pipe.data == "es > hola\n"
