"""Pruebas de la tabla de idiomas. No requiere torch/numpy/etc: languages.py
no tiene dependencias externas, así que esto corre incluso sin instalar el
resto del proyecto.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from clonavoz.languages import get_language, list_languages  # noqa: E402


def test_known_alias_resolves():
    lang = get_language("es")
    assert lang.whisper_code == "es"
    assert lang.nllb_code == "spa_Latn"
    assert lang.xtts_code == "es"


def test_language_without_cloning_has_none_xtts_code():
    lang = get_language("uk")
    assert lang.xtts_code is None
    assert lang.nllb_code == "ukr_Cyrl"


def test_raw_nllb_code_is_accepted():
    lang = get_language("ben_Beng")
    assert lang.nllb_code == "ben_Beng"
    assert lang.xtts_code is None


def test_unknown_code_raises():
    try:
        get_language("no-existe-este-idioma")
    except ValueError:
        return
    raise AssertionError("se esperaba ValueError para un código inválido")


def test_all_languages_have_unique_codes():
    codes = [lang.code for lang in list_languages()]
    assert len(codes) == len(set(codes))


if __name__ == "__main__":
    test_known_alias_resolves()
    test_language_without_cloning_has_none_xtts_code()
    test_raw_nllb_code_is_accepted()
    test_unknown_code_raises()
    test_all_languages_have_unique_codes()
    print("OK: todas las pruebas de languages.py pasaron")
