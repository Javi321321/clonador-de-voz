"""Pruebas de la elección de dispositivos: sobre todo, que clonavoz nunca
escuche del cable virtual (el caso típico de "hablo y el micrófono no se
mueve" en Windows tras instalar VB-CABLE). Usan un `sounddevice` falso, así
que no necesitan PortAudio ni hardware de audio.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import fake_sounddevice  # noqa: E402

fake_sounddevice.install()

from clonavoz import audio_devices  # noqa: E402
from clonavoz.audio_devices import (  # noqa: E402
    default_output_device,
    find_virtual_mic_input,
    find_virtual_output_device,
    is_virtual_mic_input,
    list_devices,
    resolve_input_device,
    virtual_mic_name,
)


def test_real_default_microphone_is_used_as_is():
    fake_sounddevice.windows_with_vb_cable(default_input=2)
    assert resolve_input_device(None) == (2, [])


def test_virtual_cable_as_default_microphone_is_replaced_by_the_real_one():
    # Windows suele dejar "CABLE Output" como micrófono predeterminado al
    # instalar VB-CABLE: desde ahí no se escucha la voz del usuario.
    fake_sounddevice.windows_with_vb_cable(default_input=1)
    index, notes = resolve_input_device(None)
    assert index == 2
    assert len(notes) == 1
    assert "CABLE Output" in notes[0] and "[2] Micrófono (Realtek(R) Audio)" in notes[0]


def test_explicit_virtual_cable_input_is_kept_but_warned():
    fake_sounddevice.windows_with_vb_cable()
    index, notes = resolve_input_device(1)
    assert index == 1
    assert len(notes) == 1 and "micrófono virtual" in notes[0]


def test_explicit_real_microphone_has_no_warnings():
    fake_sounddevice.windows_with_vb_cable()
    assert resolve_input_device(11) == (11, [])


def test_microphone_by_its_windows_name():
    # La ventana de clonavoz pasa el nombre que muestra Windows; con MME viene
    # cortado a 31 letras, y el mismo micrófono aparece en cada API.
    fake_sounddevice.windows_with_vb_cable(default_input=1)
    assert resolve_input_device("Micrófono (Realtek(R) Audio)") == (2, [])
    assert resolve_input_device("realtek") == (2, [])  # una parte, escrita a mano
    index, notes = resolve_input_device("CABLE Output (VB-Audio Virtual Cable)")
    assert index == 1 and "micrófono virtual" in notes[0]  # el de MME, aunque su nombre está cortado


def test_microphone_name_that_is_not_there_falls_back_to_a_real_one():
    fake_sounddevice.windows_with_vb_cable(default_input=1)
    index, notes = resolve_input_device("Micrófono (USB Audio)")
    assert index == 2
    assert "no se encontró el micrófono 'Micrófono (USB Audio)'" in notes[0]


def test_virtual_default_without_real_microphone_warns_and_keeps_default():
    d = fake_sounddevice.device
    fake_sounddevice.configure(
        [d("Microsoft Sound Mapper - Input", inputs=2), d("CABLE Output (VB-Audio Virtual ", inputs=2)],
        default_input=1,
    )
    index, notes = resolve_input_device(None)
    assert index == 1
    assert len(notes) == 1 and "--input-device" in notes[0]


def test_virtual_output_prefers_vb_cable_over_voicemeeter(monkeypatch):
    monkeypatch.setattr(audio_devices.platform, "system", lambda: "Windows")
    d = fake_sounddevice.device
    fake_sounddevice.configure(
        [
            d("Voicemeeter Input (VB-Audio Voicemeeter VAIO)", outputs=8),
            d("Altavoces (Realtek(R) Audio)", outputs=2),
            d("CABLE Input (VB-Audio Virtual C", outputs=8),
        ]
    )
    assert find_virtual_output_device().index == 2


def test_virtual_output_on_windows_list_is_the_mme_cable_input(monkeypatch):
    monkeypatch.setattr(audio_devices.platform, "system", lambda: "Windows")
    fake_sounddevice.windows_with_vb_cable()
    assert find_virtual_output_device().index == 4


def test_virtual_mic_name_is_the_other_end_of_the_cable():
    assert virtual_mic_name("CABLE Input (VB-Audio Virtual C") == "CABLE Output"
    assert virtual_mic_name("CABLE In 16ch (VB-Audio Virtual Cable)") == "CABLE Output"
    assert virtual_mic_name("CABLE-A Input (VB-Audio Cable A)") == "CABLE-A Output"
    assert virtual_mic_name("BlackHole 2ch") == "BlackHole 2ch"
    assert virtual_mic_name("Altavoces (Realtek(R) Audio)") is None


def test_find_virtual_mic_input_prefers_same_hostapi():
    fake_sounddevice.windows_with_vb_cable()
    devices = list_devices()
    assert find_virtual_mic_input(devices[4]).index == 1  # MME
    assert find_virtual_mic_input(devices[9]).index == 10  # WASAPI
    assert find_virtual_mic_input(devices[5]) is None  # parlantes: no es un cable


def test_vb_cable_input_with_the_generic_speakers_name(monkeypatch):
    # Así aparece VB-CABLE en un Windows real de GitHub Actions (y en algunas
    # PCs): la entrada se llama "Speakers"/"Altavoces (VB-Audio Virtual Cable)"
    # en vez de "CABLE Input". Tiene que elegirse igual, antes que la de 16 canales.
    monkeypatch.setattr(audio_devices.platform, "system", lambda: "Windows")
    d = fake_sounddevice.device
    for speakers in ("Speakers (VB-Audio Virtual Cabl", "Altavoces (VB-Audio Virtual Ca"):
        fake_sounddevice.configure(
            [
                d("Microsoft Sound Mapper - Input", 0, inputs=2),  # 0
                d("CABLE Output (VB-Audio Virtual ", 0, inputs=8),  # 1
                d("Microsoft Sound Mapper - Output", 0, outputs=2),  # 2
                d("CABLE In 16 Ch (VB-Audio Virtua", 0, outputs=16),  # 3
                d(speakers, 0, outputs=8),  # 4
            ],
            default_input=1,
            default_output=4,
        )
        output = find_virtual_output_device()
        assert output.index == 4
        assert virtual_mic_name(output.name) == "CABLE Output"
        assert find_virtual_mic_input(output).index == 1
    assert is_virtual_mic_input("Microphone (VB-Audio Virtual Cable)")


def test_find_virtual_mic_input_picks_the_same_cable_when_there_are_two():
    # Dos VB-CABLE instalados: Windows llama "2- ..." al segundo, y MME corta
    # los nombres a 31 letras.
    d = fake_sounddevice.device
    fake_sounddevice.configure(
        [
            d("CABLE Output (2- VB-Audio Virtu", 0, inputs=8),  # 0
            d("CABLE Output (VB-Audio Virtual ", 0, inputs=8),  # 1
            d("CABLE Input (VB-Audio Virtual C", 0, outputs=8),  # 2
            d("CABLE Input (2- VB-Audio Virtua", 0, outputs=8),  # 3
            d("CABLE In 16 Ch (2- VB-Audio Vir", 0, outputs=16),  # 4
        ]
    )
    devices = list_devices()
    assert find_virtual_mic_input(devices[2]).index == 1
    assert find_virtual_mic_input(devices[3]).index == 0
    assert find_virtual_mic_input(devices[4]).index == 0


def test_is_virtual_mic_input():
    assert is_virtual_mic_input("CABLE Output (VB-Audio Virtual ")
    assert is_virtual_mic_input("BlackHole 2ch")
    assert is_virtual_mic_input("Monitor of ClonaVoz_Mic")
    assert not is_virtual_mic_input("Micrófono (Realtek(R) Audio)")
    assert not is_virtual_mic_input("Headset Microphone (Jabra Evolve 20)")


def test_default_output_device_is_the_system_speakers():
    fake_sounddevice.windows_with_vb_cable()
    assert default_output_device().name == "Altavoces (Realtek(R) Audio)"
    fake_sounddevice.configure([], default_output=None)
    assert default_output_device() is None
