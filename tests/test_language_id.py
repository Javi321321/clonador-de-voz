"""En qué idioma te hablan: palabras, prioridad y seguimiento de la conversación."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from clonavoz.language_id import LanguageTracker, text_evidence  # noqa: E402


def best(probabilities):
    return max(probabilities, key=probabilities.get)


def test_common_words_tell_the_language():
    assert best(text_evidence("Oi, tudo bem? Faz tempo que a gente não se fala.")) == "pt"
    assert best(text_evidence("Hi! How are you doing today?")) == "en"
    assert best(text_evidence("¿Me podés repetir la última parte?")) == "es"
    assert text_evidence("こんにちは") == {}  # sin lista para ese idioma: no dice nada
    assert text_evidence("123 ...") == {}


def test_priority_languages_win_close_calls_but_not_clear_ones():
    tracker = LanguageTracker(priority=("en", "pt", "es"))
    # Por el sonido, 30% "rumano" y 10% inglés: gana el inglés (más probable en tus llamadas).
    assert tracker.update({"ro": 0.3, "en": 0.1, "pt": 0.05}, 3.0) == "en"
    # Francés claro: se reconoce aunque no esté entre los prioritarios.
    french = LanguageTracker(priority=("en", "pt", "es"))
    assert french.update({"fr": 0.95, "en": 0.02}, 3.0) == "fr"


def test_one_doubtful_phrase_does_not_change_the_language():
    tracker = LanguageTracker()
    for _ in range(3):
        tracker.update({"en": 0.9, "pt": 0.05, "es": 0.05}, 3.0)
    # una frase corta que por el sonido parece portugués, sin texto que lo confirme
    assert tracker.update({"en": 0.3, "pt": 0.6, "es": 0.1}, 1.0) == "en"


def test_sound_and_words_agreeing_switch_at_once():
    tracker = LanguageTracker()
    for _ in range(3):
        tracker.update({"en": 0.9, "pt": 0.05, "es": 0.05}, 3.0)
    assert tracker.update({"en": 0.3, "pt": 0.6, "es": 0.1}, 1.0, "Oi, tudo bem?") == "pt"


def test_noise_can_be_forgotten():
    tracker = LanguageTracker()
    tracker.update({"en": 0.9, "pt": 0.1}, 3.0)
    tracker.update({"pt": 0.99, "en": 0.01}, 3.0)
    assert tracker.current == "pt"
    tracker.revert()
    assert tracker.current == "en"
    assert tracker.heard({"pt": 0.7, "en": 0.2}) == "pt"
