"""En qué idioma te está hablando la otra persona, para traducírtelo.

Whisper lo reconoce por el sonido, pero con frases cortas se equivoca bastante:
en nuestras pruebas (audiolibros en inglés, portugués y español), con 2 s de
voz acertó el 86% de las veces (confunde portugués con inglés o español), y
con frases enteras, el 99%. Para que sea confiable se juntan tres cosas:

- Prioridad: los idiomas en los que es más probable que te hablen (inglés y
  portugués, y el tuyo) cuentan 10 veces más que los otros ~40, que igual se
  reconocen si se nota claramente que son otro idioma.
- Las palabras: si el texto se entendió sin saber el idioma (con Parakeet),
  las palabras más comunes de cada idioma ("the", "você", "está"...) lo
  confirman. Con 2 s de voz, el acierto sube del 86% al 94.5%, y con 3 s, del
  94% al 98.6%.
- La conversación: en una llamada la otra persona casi siempre habla el mismo
  idioma, así que cada frase suma a lo que venían diciendo las anteriores (y
  pesa más cuanto más larga es). Después de 2 o 3 frases el error queda en
  1-2%, y si la persona cambia de idioma, en 2 o 3 frases se nota (o en la
  primera, si el sonido y las palabras coinciden con seguridad).
"""
from __future__ import annotations

import math
import re

# Idiomas en los que clonavoz traduce (códigos de Whisper), para no confundirse
# con idiomas que no puede traducir ni decir.
KNOWN = (
    "es", "en", "pt", "fr", "de", "it", "nl", "pl", "ru", "tr", "ar", "zh", "ja", "ko", "hu", "cs", "hi", "uk",
    "sv", "no", "da", "fi", "el", "ro", "bg", "sk", "hr", "sr", "ca", "eu", "gl", "he", "vi", "th", "id", "ms",
    "bn", "fa", "ur", "sw",
)
PRIORITY_WEIGHT = 10.0
_DECAY = 0.5  # cuánto pesa lo que venían diciendo frente a la frase nueva
_TEXT_WEIGHT = 1.5

# Las palabras más frecuentes de cada idioma (y letras típicas), para confirmar
# el idioma de un texto. Solo se usan entre idiomas que tienen lista.
_WORDS = {
    "es": """de la que el en y a los se del las un por con no una su para es al lo como más pero sus le ya o
        fue este ha sí porque esta son entre cuando muy sin sobre también me hasta hay donde quien desde todo nos
        durante todos uno les ni contra otros ese eso ante ellos esto mí antes algunos qué unos yo otro otras otra
        él tanto esa estos mucho quienes nada muchos cual poco ella estar estas algunas algo nosotros mi mis tú te
        ti tu tus ellas vos usted ustedes está están estoy tengo tiene hola gracias bueno dale cómo dónde cuándo
        pues entonces aquí ahí hoy mañana ayer bien vamos quiero puedo podés sabés""",
    "pt": """de a o que e do da em um para é com não uma os no se na por mais as dos como mas foi ao ele das tem
        à seu sua ou ser quando muito há nos já está eu também só pelo pela até isso ela entre era depois sem
        mesmo aos ter seus quem nas me esse eles estão você tinha foram essa num nem suas meu às minha têm numa
        pelos elas havia seja qual será nós tenho lhe deles essas esses pelas este fosse dele tu te vocês lhes
        meus minhas teu tua nosso nossa dela esta estes estas aquele aquela isto aquilo estou estamos obrigado
        obrigada oi olá tudo bem então aqui agora hoje amanhã ontem gente pra sim vou vamos quero posso""",
    "en": """the of and to a in is you that it he was for on are as with his they i at be this have from or one
        had by but not what all were we when your can said there an each which she do how their if will up other
        about out many then them these so some her would make like him into time has look two more see no way
        could people my than first been who its now find long down day did get come made may part hello hi yes
        thanks thank okay ok please sorry it's i'm don't can't we're you're that's what's""",
    "fr": """le de un être et à il avoir ne je son que se qui ce dans en du elle au pour pas plus par sur faire
        avec tout nous vous mais ou où les des une est sont ces cette bonjour merci oui très aussi bien""",
    "it": """il di che e la a per un in è non una sono mi si lo ho le ma ci con da questo come io ti del gli
        anche della più al perché cosa bene grazie ciao sì molto sei siamo""",
    "de": """der die und in den von zu das mit sich des auf für ist im dem nicht ein eine als auch es an werden
        aus er hat dass sie nach wird bei einer um am sind noch wie einem über einen so zum ich du wir ihr
        haben danke bitte ja nein gut hallo""",
}
_WORD_SETS = {code: set(words.split()) for code, words in _WORDS.items()}
_LETTERS = {
    "pt": ("ão", "õe", "ç", "nh", "lh", "ê", "ô", "ã"),
    "es": ("ñ", "ll", "ió", "¿", "¡"),
    "en": ("th", "w", "sh"),
    "fr": ("eau", "ç", "è", "ê", "ou"),
    "de": ("sch", "ß", "ä", "ö", "ü", "ch"),
    "it": ("gli", "zz", "cc", "ò", "ù"),
}
_WORD = re.compile(r"[^\W\d_]+(?:'[^\W\d_]+)?")


def text_evidence(text: str) -> dict[str, float]:
    """Probabilidad de cada idioma (de los que tienen lista) por las palabras
    comunes y letras típicas del texto. Vacío si no dice nada útil."""
    lowered = text.lower()
    words = _WORD.findall(lowered)
    if not words:
        return {}
    scores = {
        code: sum(word in _WORD_SETS[code] for word in words) + 0.5 * sum(lowered.count(g) for g in _LETTERS[code])
        for code in _WORDS
    }
    top = max(scores.values())
    if top == 0:
        return {}
    weights = {code: math.exp(1.2 * (score - top)) for code, score in scores.items()}
    total = sum(weights.values())
    return {code: weight / total for code, weight in weights.items()}


class LanguageTracker:
    """Sigue en qué idioma te hablan. `priority`: los idiomas más probables
    (ej. inglés, portugués y el tuyo); `candidates`: entre cuáles elegir (por
    defecto, todos los que clonavoz traduce)."""

    def __init__(self, priority: tuple[str, ...] = ("en", "pt", "es"), candidates: tuple[str, ...] = KNOWN) -> None:
        self.priority = tuple(priority)
        self.candidates = tuple(dict.fromkeys((*priority, *candidates)))
        self._evidence = dict.fromkeys(self.candidates, 0.0)
        self.current: str | None = None
        self._before: tuple[dict[str, float], str | None] | None = None

    def _audio(self, probabilities: dict[str, float]) -> dict[str, float]:
        weighted = {
            code: max(probabilities.get(code, 0.0), 1e-6) * (PRIORITY_WEIGHT if code in self.priority else 1.0)
            for code in self.candidates
        }
        total = sum(weighted.values())
        return {code: value / total for code, value in weighted.items()}

    def heard(self, probabilities: dict[str, float]) -> str:
        """El idioma más probable por el sonido de esta frase sola."""
        audio = self._audio(probabilities)
        return max(audio, key=audio.get)

    def update(self, probabilities: dict[str, float], seconds: float, text: str | None = None) -> str:
        """Suma una frase: lo que dijo Whisper por el sonido (`probabilities`),
        cuánto duró y, si se entendió sin saber el idioma, su texto. Devuelve el
        idioma en que te están hablando."""
        audio = self._audio(probabilities)
        words = text_evidence(text) if text else {}
        # Las palabras solo se comparan entre idiomas con lista: si por el sonido
        # es claramente otro (ej. japonés), no se usan.
        if words and max(audio, key=audio.get) not in words:
            words = {}
        floor = min(words.values()) if words else 1.0
        weight = min(1.5, seconds / 2.0)
        self._before = (dict(self._evidence), self.current)
        for code in self.candidates:
            score = math.log(audio[code]) + _TEXT_WEIGHT * math.log(words.get(code, floor))
            self._evidence[code] = _DECAY * self._evidence[code] + weight * score
        # Si el sonido y las palabras coinciden, cambia ya (ej. "Oi, tudo bem?"
        # después de varias frases en inglés), sin esperar más frases: que las
        # dos cosas se equivoquen en lo mismo casi no pasa (en nuestras pruebas,
        # con esto la primera frase en el idioma nuevo se entiende bien el 84% de
        # las veces, contra el 53%, y hablando siempre en el mismo idioma no se
        # equivoca más).
        heard = max(audio, key=audio.get)
        if words and heard == max(words, key=words.get) and audio[heard] > 0.1 and words[heard] > 0.34:
            self._evidence[heard] = max(self._evidence[heard], max(self._evidence.values()) + 1.0)
        self.current = max(self._evidence, key=self._evidence.get)
        return self.current

    def revert(self) -> None:
        """Olvida la última frase (ej. resultó ser ruido, sin palabras)."""
        if self._before is not None:
            self._evidence, self.current = self._before
            self._before = None
