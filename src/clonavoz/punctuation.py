"""La puntuación que no pone Vosk (ver vosk_asr.py): mayúscula al principio,
punto al final y, sobre todo, los signos de pregunta, que cambian la
traducción: sin ellos, "me podés repetir la última parte" se traduce "You can
repeat the last part" en vez de "Can you repeat the last part?".

Una frase es una pregunta si empieza como una: una palabra interrogativa
(qué, cómo, cuándo...) entre las primeras tres ("hola cómo estás"), o un
verbo con el que se pide algo (podés, me podés...) al principio. Las palabras
están en español e inglés. (Mirar si la entonación sube al final, como en
"¿vos venís mañana?", en nuestras pruebas acertaba poco y marcaba como
pregunta una de cada diez afirmaciones: no se usa.)
"""
from __future__ import annotations

_INTERROGATIVES = {
    "es": {
        "qué", "cómo", "cuándo", "dónde", "adónde", "quién", "quiénes", "cuál", "cuáles",
        "cuánto", "cuánta", "cuántos", "cuántas",
    },
    "en": {"what", "where", "when", "why", "who", "whom", "whose", "which", "how"},
}
_TWO_WORD_INTERROGATIVES = {"es": {("por", "qué"), ("para", "qué")}, "en": set()}
# Al principio de la frase (después de un "y", "che", "bueno"... como mucho).
_QUESTION_STARTS = {
    "es": (
        "podés", "puedes", "podrías", "podría", "podemos", "pueden", "querés", "quieres",
        "me podés", "me puedes", "me puede", "me podrías", "me podría", "nos podés", "nos puedes", "nos puede",
        "me escuchás", "me escuchas", "me oís", "me oyes",
    ),
    "en": (
        "do", "does", "did", "can", "could", "would", "will", "should", "shall",
        "is", "are", "was", "were", "have", "has", "am", "may",
    ),
}
_FILLERS = {
    "es": {"y", "che", "bueno", "entonces", "pero", "oye", "mirá", "mira", "decime", "dime", "hola", "vos"},
    "en": {"and", "so", "well", "hey", "hi", "hello", "but", "then", "you"},
}


def is_question(text: str, language_code: str) -> bool:
    words = text.lower().split()
    fillers = _FILLERS.get(language_code, set())
    start = 0
    while start < min(2, len(words)) and words[start] in fillers:
        start += 1
    for phrase in _QUESTION_STARTS.get(language_code, ()):
        if words[start : start + len(phrase.split())] == phrase.split():
            return True
    first = words[:3]
    if any(word in _INTERROGATIVES.get(language_code, set()) for word in first):
        return True
    pairs = set(zip(first, first[1:], strict=False))
    return bool(pairs & _TWO_WORD_INTERROGATIVES.get(language_code, set()))


def restore(text: str, language_code: str) -> str:
    """`text` (sin puntuación) con mayúscula, y punto o signos de pregunta."""
    text = text.strip()
    if not text:
        return text
    question = is_question(text, language_code)
    text = text[0].upper() + text[1:]
    if not question:
        return text + "."
    return ("¿" if language_code == "es" else "") + text + "?"
