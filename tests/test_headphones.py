"""Dónde escuchás lo que te dicen: nunca en el cable virtual (eso lo escucharía
la otra persona), ni en algo que apunte a él."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import fake_sounddevice  # noqa: E402

fake_sounddevice.install()

from clonavoz import audio_devices, cli  # noqa: E402


def cable_as_default_output(monkeypatch, *others):
    monkeypatch.setattr(audio_devices.platform, "system", lambda: "Windows")
    d = fake_sounddevice.device
    fake_sounddevice.configure(
        [
            d("Microsoft Sound Mapper - Input", inputs=2),
            d("CABLE Output (VB-Audio Virtual ", inputs=8),
            d("Microsoft Sound Mapper - Output", outputs=2),
            d("Speakers (VB-Audio Virtual Cabl", outputs=8),
            *others,
        ],
        default_input=1,
        default_output=3,
    )


def test_the_sound_mapper_is_not_your_headphones(monkeypatch):
    # Así es la máquina de prueba de Windows: el único parlante es el cable, y el
    # mapeador de Windows apunta a él. Lo que te dicen se ve solo en pantalla.
    cable_as_default_output(monkeypatch)
    assert cli._resolve_headphones(None) is None


def test_with_the_cable_as_default_output_your_real_headphones_are_used(monkeypatch):
    cable_as_default_output(monkeypatch, fake_sounddevice.device("Auriculares (Realtek(R) Audio)", outputs=2))
    assert cli._resolve_headphones(None).name == "Auriculares (Realtek(R) Audio)"


def test_headphones_before_a_monitor_without_speakers(monkeypatch):
    d = fake_sounddevice.device
    cable_as_default_output(
        monkeypatch, d("DELL U2419H (NVIDIA High Definition Audio)", outputs=2), d("Auriculares (USB Audio)", outputs=2)
    )
    assert cli._resolve_headphones(None).name == "Auriculares (USB Audio)"
