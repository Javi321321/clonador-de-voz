"""La línea de estado: en la consola se redibuja; para la ventana de
clonavoz (CLONAVOZ_ESTADO=1, salida por un pipe) sale en líneas "@@ ..."."""
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from clonavoz import console  # noqa: E402
from clonavoz.cli import _device  # noqa: E402


def test_without_a_terminal_the_status_is_not_drawn(monkeypatch):
    monkeypatch.delenv("CLONAVOZ_ESTADO", raising=False)
    out = io.StringIO()
    status = console.StatusLine(out)
    status.update("Mic [###---] -20 dB")
    status.print("  Vos (es) > hola")
    assert out.getvalue() == "  Vos (es) > hola\n"


def test_for_the_window_the_status_goes_in_a_few_lines_per_second(monkeypatch):
    monkeypatch.setenv("CLONAVOZ_ESTADO", "1")
    clock = iter([10.0, 10.05, 10.3])
    monkeypatch.setattr(console.time, "monotonic", lambda: next(clock))
    out = io.StringIO()
    status = console.StatusLine(out)
    status.update("quedan 14.9 s")
    status.update("quedan 14.8 s")  # muy seguido: no se manda
    status.update("quedan 14.6 s")
    status.print("Muestra de voz guardada")
    status.clear()
    assert out.getvalue().splitlines() == ["@@ quedan 14.9 s", "@@ quedan 14.6 s", "Muestra de voz guardada", "@@ "]


def test_a_microphone_by_number_or_by_name():
    assert _device("3") == 3
    assert _device(" Micrófono (Realtek(R) Audio) ") == "Micrófono (Realtek(R) Audio)"
