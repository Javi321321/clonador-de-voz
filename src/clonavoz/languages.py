"""Tabla de idiomas soportados y sus códigos para cada motor del pipeline:

- whisper_code: código para transcripción con faster-whisper (~99 idiomas)
- nllb_code: código FLORES-200 para traducción con NLLB-200 (~200 idiomas)
- xtts_code: código para clonar tu voz con XTTS-v2 (solo ~17 idiomas hoy;
  None si ese idioma no tiene clonación disponible todavía)

Puedes usar cualquier alias corto de esta tabla, o pasar directamente un
código NLLB (formato FLORES-200, ej. "ben_Beng" para bengalí) si tu idioma
no está en la lista — la traducción funcionará igual con cualquiera de los
~200 idiomas de NLLB-200, aunque no tenga alias corto ni clonación de voz.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Language:
    code: str
    name: str
    whisper_code: str
    nllb_code: str
    xtts_code: str | None


# Idiomas en los que XTTS-v2 sí puede clonar tu timbre de voz. Es una
# limitación real de los modelos abiertos actuales, no una restricción
# artificial: hoy no existe un modelo de clonación de voz abierto con
# cobertura más amplia y buena calidad.
LANGUAGES: list[Language] = [
    # --- con clonación de voz (XTTS-v2) ---
    Language("es", "Español", "es", "spa_Latn", "es"),
    Language("en", "Inglés", "en", "eng_Latn", "en"),
    Language("pt", "Portugués", "pt", "por_Latn", "pt"),
    Language("fr", "Francés", "fr", "fra_Latn", "fr"),
    Language("de", "Alemán", "de", "deu_Latn", "de"),
    Language("it", "Italiano", "it", "ita_Latn", "it"),
    Language("nl", "Neerlandés", "nl", "nld_Latn", "nl"),
    Language("pl", "Polaco", "pl", "pol_Latn", "pl"),
    Language("ru", "Ruso", "ru", "rus_Cyrl", "ru"),
    Language("tr", "Turco", "tr", "tur_Latn", "tr"),
    Language("ar", "Árabe", "ar", "arb_Arab", "ar"),
    Language("zh", "Chino (mandarín)", "zh", "zho_Hans", "zh-cn"),
    Language("ja", "Japonés", "ja", "jpn_Jpan", "ja"),
    Language("ko", "Coreano", "ko", "kor_Hang", "ko"),
    Language("hu", "Húngaro", "hu", "hun_Latn", "hu"),
    Language("cs", "Checo", "cs", "ces_Latn", "cs"),
    Language("hi", "Hindi", "hi", "hin_Deva", "hi"),
    # --- transcripción + traducción, pero sin clonación (voz neutra Piper) ---
    Language("uk", "Ucraniano", "uk", "ukr_Cyrl", None),
    Language("sv", "Sueco", "sv", "swe_Latn", None),
    Language("no", "Noruego", "no", "nob_Latn", None),
    Language("da", "Danés", "da", "dan_Latn", None),
    Language("fi", "Finlandés", "fi", "fin_Latn", None),
    Language("el", "Griego", "el", "ell_Grek", None),
    Language("ro", "Rumano", "ro", "ron_Latn", None),
    Language("bg", "Búlgaro", "bg", "bul_Cyrl", None),
    Language("sk", "Eslovaco", "sk", "slk_Latn", None),
    Language("hr", "Croata", "hr", "hrv_Latn", None),
    Language("sr", "Serbio", "sr", "srp_Cyrl", None),
    Language("ca", "Catalán", "ca", "cat_Latn", None),
    Language("eu", "Euskera", "eu", "eus_Latn", None),
    Language("gl", "Gallego", "gl", "glg_Latn", None),
    Language("he", "Hebreo", "he", "heb_Hebr", None),
    Language("vi", "Vietnamita", "vi", "vie_Latn", None),
    Language("th", "Tailandés", "th", "tha_Thai", None),
    Language("id", "Indonesio", "id", "ind_Latn", None),
    Language("ms", "Malayo", "ms", "zsm_Latn", None),
    Language("bn", "Bengalí", "bn", "ben_Beng", None),
    Language("fa", "Persa/Farsi", "fa", "pes_Arab", None),
    Language("ur", "Urdu", "ur", "urd_Arab", None),
    Language("sw", "Swahili", "sw", "swh_Latn", None),
]

_BY_CODE = {lang.code: lang for lang in LANGUAGES}


def get_language(code: str) -> Language:
    if code in _BY_CODE:
        return _BY_CODE[code]

    # Código NLLB (FLORES-200) directo, ej. "ben_Beng": traducción funciona
    # aunque no tenga alias corto ni clonación de voz en esta tabla.
    if "_" in code and code[:3].isalpha():
        whisper_guess = code.split("_")[0][:2]
        return Language(code=code, name=code, whisper_code=whisper_guess, nllb_code=code, xtts_code=None)

    raise ValueError(
        f"Idioma '{code}' no reconocido. Ejecuta `clonavoz languages` para ver la "
        "lista de alias, o pasa un código NLLB (FLORES-200) directamente, ej. 'ben_Beng'."
    )


def list_languages() -> list[Language]:
    return list(LANGUAGES)
