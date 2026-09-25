"""Traducción simultánea: decide dónde cortar lo que venís diciendo para
traducirlo sin esperar a que termines, como hace un intérprete.

Mientras hablás, cada ~0.7 s se reconoce la frase en curso con marcas de
tiempo por palabra (Parakeet las da, y además pone comas y puntos). Cuando
aparece el final de una idea (una coma, un punto, dos puntos...) con al menos
unas palabras antes, se corta justo ahí: esa parte se traduce y se dice con tu
voz mientras seguís hablando. Si pasan varios segundos sin ninguna, se corta
entre dos palabras. Y en una pausa corta, si lo reconocido termina en punto
(o en signo de pregunta o exclamación), la oración ya terminó: sale enseguida,
sin esperar el medio segundo de silencio de siempre.

No se corta en lo último que se escuchó (`GUARD_SECONDS`): el reconocimiento
de las últimas palabras todavía puede cambiar con lo que viene.
"""
from __future__ import annotations

import numpy as np

_PUNCTUATION = set(",.;:?!")
_SENTENCE_END = set(".?!")


class ClauseSplitter:
    GUARD_SECONDS = 0.35
    MIN_WORDS = 3  # no cortar pedacitos de una o dos palabras
    MAX_WAIT_SECONDS = 4.0  # sin coma ni punto, se corta igual entre dos palabras

    def __init__(self, recognize_timed, sample_rate: int = 16000) -> None:
        """`recognize_timed(audio) -> (tokens, tiempos)`: los pedazos de texto
        reconocidos (las palabras empiezan con espacio) y cuándo empieza cada
        uno, en segundos."""
        self._recognize = recognize_timed
        self._rate = sample_rate

    def __call__(self, audio: np.ndarray, final: bool) -> tuple[int, str] | None:
        try:
            tokens, times = self._recognize(audio)
        except Exception:  # noqa: BLE001 - si falla, se corta como siempre (por pausas)
            return None
        if not tokens:
            return None
        duration = len(audio) / self._rate
        if final:
            text = "".join(tokens).strip()
            return (len(audio), text) if text and text[-1] in _SENTENCE_END else None

        words = 0
        cut = None  # (índice del último pedazo incluido, tiempo de corte)
        last_word_cut = None
        for i, (token, start) in enumerate(zip(tokens, times, strict=True)):
            if token.startswith(" ") or i == 0:
                if start <= duration - self.GUARD_SECONDS and words >= self.MIN_WORDS:
                    last_word_cut = (i - 1, start)
                words += 1
            if token.strip() in _PUNCTUATION and words >= self.MIN_WORDS:
                following = times[i + 1] if i + 1 < len(times) else duration
                if following <= duration - self.GUARD_SECONDS:
                    cut = (i, following)
        if cut is None and duration >= self.MAX_WAIT_SECONDS:
            cut = last_word_cut
        if cut is None:
            return None
        last, at = cut
        text = "".join(tokens[: last + 1]).strip()
        if not text:
            return None
        # Un poquito antes de que empiece la palabra siguiente.
        return max(1, int((at - 0.04) * self._rate)), text
