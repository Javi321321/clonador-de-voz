"""Voces de Piper: síntesis de voz muy liviana (ONNX), más rápida que tiempo
real incluso en computadoras de bajos recursos. Se usa de dos formas:

- motor "openvoice" (por defecto sin GPU): Piper dice la frase traducida y el
  conversor de OpenVoice le pone tu timbre (ver `openvoice.py`);
- motor "xtts": como voz neutra para los idiomas que XTTS-v2 no clona.

Para cada idioma hay una voz grave y, si existe, una aguda: se usa la más
parecida al tono de tu muestra de voz, porque el conversor de timbre funciona
mejor partiendo de una voz de tono parecido. Las voces se descargan una sola
vez (necesitas internet la primera vez que uses un idioma nuevo) y después
quedan en caché local para funcionar 100% sin conexión.

Algunas voces necesitan un paquete extra de Python para leer su idioma (por
ejemplo `pyopenjtalk` para japonés); si falta, el error dice cuál instalar.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np

from .paths import data_dir

_HIGH_PITCH_HZ = 160  # a partir de acá se considera una voz aguda

# Voces del catálogo público de Piper (https://huggingface.co/rhasspy/piper-voices),
# calidad "medium", priorizando licencias libres (CC0, dominio público, CC-BY):
# (grave, aguda), o una sola si el idioma no tiene otra. Puedes cambiar la voz
# de un idioma creando piper_voices.json en la carpeta de datos (~/.clonavoz, o
# `datos` en la versión portable), ej:
#   {"bn": "bn_BD-nombre_de_la_voz-medium"}
_VOICES: dict[str, tuple[str, ...]] = {
    "es": ("es_ES-davefx-medium", "es_MX-ald-medium"),
    "en": ("en_US-joe-medium", "en_US-ljspeech-medium"),
    "pt": ("pt_BR-cadu-medium", "pt_BR-faber-medium"),
    "fr": ("fr_FR-mls-medium", "fr_FR-siwis-medium"),
    "de": ("de_DE-thorsten-medium",),
    "it": ("it_IT-serena-medium",),
    "nl": ("nl_NL-ronnie-medium", "nl_BE-nathalie-medium"),
    "pl": ("pl_PL-darkman-medium", "pl_PL-gosia-medium"),
    "ru": ("ru_RU-denis-medium", "ru_RU-irina-medium"),
    "tr": ("tr_TR-dfki-medium",),
    "ar": ("ar_JO-kareem-medium",),
    "zh": ("zh_CN-huayan-medium",),
    "ja": ("ja_JP-hi_fi_captain-medium",),
    "ko": ("ko_KR-kss-medium",),
    "hu": ("hu_HU-imre-medium", "hu_HU-anna-medium"),
    "cs": ("cs_CZ-jirka-medium", "cs_CZ-kasandra-medium"),
    "hi": ("hi_IN-pratham-medium", "hi_IN-priyamvada-medium"),
    "uk": ("uk_UA-ukrainian_tts-medium",),
    "sv": ("sv_SE-nst-medium", "sv_SE-lisa-medium"),
    "no": ("no_NO-talesyntese-medium",),
    "da": ("da_DK-talesyntese-medium",),
    "fi": ("fi_FI-harri-medium",),
    "el": ("el_GR-rapunzelina-medium",),
    "ro": ("ro_RO-mihai-medium",),
    "bg": ("bg_BG-dimitar-medium",),
    "sk": ("sk_SK-lili-medium",),
    "sr": ("sr_RS-serbski_institut-medium",),
    "ca": ("ca_ES-upc_ona-medium",),
    "eu": ("eu_ES-antton-medium", "eu_ES-maider-medium"),
    "he": ("he_IL-saspeech-medium",),
    "vi": ("vi_VN-vais1000-medium",),
    "th": ("th_TH-tsync2-medium",),
    "id": ("id_ID-news_tts-medium",),
    "bn": ("bn_BD-google-medium",),
    "fa": ("fa_IR-amir-medium",),
    "ur": ("ur_PK-fasih-medium", "ur_PK-aegis_female-medium"),
    "sw": ("sw_CD-lanfrica-medium",),
}

# Para decirte lo que te dicen los demás, en tu idioma: una voz de hombre y
# una de mujer, según el tono de quien habla (ver listen.TheirVoice). La de
# mujer en español es es_MX-claude-high (licencia Apache-2.0). Para los demás
# idiomas se usan las de arriba.
LISTEN_VOICES: dict[str, tuple[str, str]] = {
    "es": ("es_ES-davefx-medium", "es_MX-claude-high"),
    "en": ("en_US-joe-medium", "en_US-ljspeech-medium"),
}


def listen_voices(language_code: str) -> tuple[str, str]:
    """(voz de hombre, voz de mujer) para decirte algo en `language_code`."""
    if language_code in _user_overrides():
        voice = _user_overrides()[language_code]
        return voice, voice
    if language_code in LISTEN_VOICES:
        return LISTEN_VOICES[language_code]
    return resolve_voice_id(language_code, None), resolve_voice_id(language_code, _HIGH_PITCH_HZ)


# Frase que cada voz lee una vez al arrancar para medir su timbre (el
# conversor necesita la "huella" de la voz de origen). Traducidas con
# NLLB-200: el contenido da igual, solo tiene que ser habla natural.
_CALIBRATION = {
    "es": "Hola, esta es una muestra corta de mi voz. Estoy leyendo estas palabras con calma y claridad, para que el programa pueda aprender cómo sueno.",
    "en": "Hello, this is a short sample of my voice. I am reading these words calmly and clearly, so that the program can learn how I sound.",
    "pt": "Olá, esta é uma pequena amostra da minha voz. Estou a ler estas palavras calmamente e claramente, para que o programa possa aprender como eu soa.",
    "fr": "Bonjour, voici un bref échantillon de ma voix. Je lis ces mots calmement et clairement, afin que le programme puisse apprendre comment je sonne.",
    "de": "Hallo, das ist eine kurze Stimmprobe. Ich lese diese Worte ruhig und deutlich, damit das Programm lernen kann, wie ich klingle.",
    "it": "Salve, questo è un breve campione della mia voce. Sto leggendo queste parole con calma e chiarezza, in modo che il programma possa imparare come suono.",
    "nl": "Hallo, dit is een kort voorbeeld van mijn stem. Ik lees deze woorden rustig en duidelijk, zodat het programma kan leren hoe ik klink.",
    "pl": "Witam, to jest krótka próbka mojego głosu. Czytam te słowa spokojnie i wyraźnie, aby program mógł nauczyć się, jak brzmię.",
    "ru": "Привет, это короткий образец моего голоса. Я читаю эти слова спокойно и ясно, чтобы программа могла узнать, как я звучу.",
    "tr": "Merhaba, bu sesimin kısa bir örneği. Bu kelimeleri sakin ve net bir şekilde okuyorum, böylece program benim sesimi öğrenebilir.",
    "ar": "مرحباً، هذه عينة قصيرة من صوتي، أقرأ هذه الكلمات بهدوء وبشكل واضح، حتى يتعلم البرنامج كيف أصوت.",
    "zh": "您好,这是我的声音的简短样本. 我平静地清晰地阅读这些词,以便程序能够学习我的声音.",
    "ja": "こんにちは,これは私の声の短いサンプルです. 私は静かにはっきりとこれらの言葉を読みます.",
    "ko": "안녕하세요, 이것은 내 목소리의 짧은 표본입니다. 저는 이 단어를 조용하고 명확하게 읽고 있습니다. 그래서 프로그램이 내 소리를 배울 수 있습니다.",
    "hu": "Szia, ez egy rövid mintam a hangomról. Nyugodtan és világosan olvasom ezeket a szavakat, hogy a program megtanulja, hogy hogyan hangzom.",
    "cs": "Ahoj, tohle je krátký vzork mého hlasu. Čtu tyto slova klidně a jasně, aby se program naučil, jak zní můj hlas.",
    "hi": "नमस्ते, यह मेरी आवाज का एक छोटा सा नमूना है. मैं इन शब्दों को शांत और स्पष्ट रूप से पढ़ रहा हूं, ताकि कार्यक्रम सीख सके कि मैं कैसे सुनता हूं।",
    "uk": "Привітання, це короткий збір мій голосу. Я читаю ці слова спокійно і чітко, щоб програма могла дізнатися, як я звучу.",
    "sv": "Hej, det här är ett kort prov av min röst. Jag läser dessa ord lugnt och tydligt, så att programmet kan lära sig hur jag låter.",
    "no": "Hei, dette er en kort prøve av stemmen min. Jeg leser disse ordene rolig og klart, slik at programmet kan lære hvordan jeg høres.",
    "da": "Hej, det her er et kort sample af min stemme. Jeg læser disse ord roligt og tydeligt, så programmet kan lære, hvordan jeg lyder.",
    "fi": "Tämä on lyhyt ääneni näyte. Luen nämä sanat rauhallisesti ja selkeästi, jotta ohjelma voi oppia, miten kuulon.",
    "el": "Γεια σας, αυτό είναι ένα μικρό δείγμα της φωνής μου. Διαβάζω αυτές τις λέξεις ήρεμα και καθαρά, ώστε το πρόγραμμα να μάθει πώς ακούγομαι.",
    "ro": "Bună, aceasta este o scurtă mostră a vocii mele. Citesc aceste cuvinte calm și clar, astfel încât programul să poată învăța cum sun.",
    "bg": "Здравейте, това е кратък образец от гласа ми. Аз чета тези думи спокойно и ясно, така че програмата може да научи как звуча.",
    "sk": "Ahoj, toto je krátka vzorka môjho hlasu. Čítam tieto slová pokojne a jasne, aby sa program mohol naučiť, ako znie.",
    "sr": "Здраво, ово је кратки узор мог гласа. Прочитао сам ове речи смирено и јасно, тако да програм може научити како звучам.",
    "ca": "Hola, aquesta és una breu mostra de la meva veu. Estem llegint aquestes paraules amb calma i claritat, perquè el programa pugui aprendre com sona.",
    "eu": "Kaixo, hau nire ahotsaren lagin laburra da. Hitzak lasai eta argi irakurtzen ari naiz, programak nire ahotsa ikas dezan.",
    "he": "שלום, זו דגימת קצרה של הקול שלי. אני קורא את המילים האלה בשקט ובבהירות, כך שהכורה תוכל ללמוד איך אני נשמע.",
    "vi": "Xin chào, đây là một mẫu ngắn của giọng nói của tôi. Tôi đang đọc những từ này một cách bình tĩnh và rõ ràng, để chương trình có thể học cách nghe tôi.",
    "th": "สวัสดีครับ นี่คือตัวอย่างสั้นของเสียงผม ผมอ่านคําเหล่านี้อย่างสงบและชัดเจน เพื่อโปรแกรมจะได้เรียนรู้เสียงผม",
    "id": "Halo, ini adalah sampel suara saya. Saya membaca kata-kata ini dengan tenang dan jelas, sehingga program dapat belajar bagaimana suara saya.",
    "bn": "হ্যালো, এটা আমার কণ্ঠের একটি সংক্ষিপ্ত নমুনা। আমি এই শব্দগুলি শান্তভাবে এবং পরিষ্কারভাবে পড়ছি, যাতে প্রোগ্রামটি আমার কণ্ঠস্বর শিখতে পারে।",
    "fa": "سلام، این یک نمونه کوتاه از صدای من است. من این کلمات را به آرامی و واضح می خوانم، تا برنامه بتواند یاد بگیرد که من چگونه صدا می زنم.",
    "ur": "ہیلو، یہ میری آواز کا ایک مختصر نمونہ ہے۔ میں ان الفاظ کو پرسکون اور واضح طور پر پڑھ رہا ہوں، تاکہ پروگرام سیکھ سکے کہ میں کس طرح سنتا ہوں۔",
    "sw": "Hi, hii ni sampuli fupi ya sauti yangu. Mimi kusoma maneno haya kwa utulivu na wazi, ili programu inaweza kujifunza jinsi mimi sauti.",
}


def median_pitch(audio: np.ndarray, sample_rate: int) -> float | None:
    """Tono medio (F0, en Hz) de una voz, por autocorrelación de los tramos
    con voz. None si no se pudo estimar (por ejemplo, si es silencio)."""
    audio = np.asarray(audio, dtype=np.float32)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    frame, hop = int(0.04 * sample_rate), int(0.01 * sample_rate)
    lo, hi = int(sample_rate / 400), int(sample_rate / 60)
    level = float(np.sqrt(np.mean(audio**2))) if len(audio) else 0.0
    pitches = []
    for start in range(0, len(audio) - frame, hop):
        x = audio[start : start + frame] - audio[start : start + frame].mean()
        if level == 0.0 or np.sqrt(np.mean(x**2)) < 0.5 * level:
            continue
        ac = np.fft.irfft(np.abs(np.fft.rfft(x, 2 * frame)) ** 2)[:frame]
        lag = lo + int(np.argmax(ac[lo:hi]))
        if ac[lag] < 0.5 * ac[0]:
            continue  # sin periodicidad clara: no es un tramo con voz
        if lag // 2 >= lo and ac[lag // 2] > 0.8 * ac[lag]:
            lag //= 2  # era el doble del período: el tono real es una octava más arriba
        pitches.append(sample_rate / lag)
    return float(np.median(pitches)) if pitches else None


def _user_overrides() -> dict:
    path = data_dir() / "piper_voices.json"
    if path.exists():
        return json.loads(path.read_text())
    return {}


def has_voice(language_code: str) -> bool:
    """Si hay una voz de Piper para ese idioma (incluidas las que agregaste vos)."""
    return language_code in _VOICES or language_code in _user_overrides()


def resolve_voice_id(language_code: str, speaker_pitch_hz: float | None = None) -> str:
    overrides = _user_overrides()
    if language_code in overrides:
        return overrides[language_code]
    if language_code in _VOICES:
        options = _VOICES[language_code]
        high = speaker_pitch_hz is not None and speaker_pitch_hz >= _HIGH_PITCH_HZ
        return options[-1] if high else options[0]
    raise RuntimeError(
        f"No hay una voz Piper configurada para el idioma '{language_code}'.\n"
        "Busca un modelo para ese idioma en https://huggingface.co/rhasspy/piper-voices "
        f"y agrégalo en {data_dir() / 'piper_voices.json'}, por ejemplo:\n"
        f'  {{"{language_code}": "xx_XX-nombre-medium"}}'
    )


def _ensure_voice_downloaded(voice_id: str) -> Path:
    voices_dir = data_dir() / "piper_voices"
    voices_dir.mkdir(parents=True, exist_ok=True)
    onnx_path = voices_dir / f"{voice_id}.onnx"
    if not onnx_path.exists():
        print(f"[clonavoz] Descargando voz Piper '{voice_id}' (una sola vez)...")
        subprocess.run(
            [sys.executable, "-m", "piper.download_voices", voice_id, "--download-dir", str(voices_dir)],
            check=True,
        )
    return onnx_path


def ensure_voice(voice_id: str) -> None:
    """Descarga esa voz si falta."""
    _ensure_voice_downloaded(voice_id)


def download_voices(language_code: str) -> list[str]:
    """Descarga todas las voces base de un idioma (grave y aguda), para poder
    usarlo después sin internet sea cual sea el tono de la muestra de voz."""
    overrides = _user_overrides()
    if language_code in overrides:
        voice_ids = [overrides[language_code]]
    elif language_code in _VOICES:
        voice_ids = list(_VOICES[language_code])
    else:
        voice_ids = [resolve_voice_id(language_code)]  # lanza el error explicativo
    for voice_id in voice_ids:
        _ensure_voice_downloaded(voice_id)
    return voice_ids


class PiperSynthesizer:
    """`voices`: voz a usar en cada idioma (código -> voz), en vez de elegirla
    por el tono."""

    def __init__(self, speaker_pitch_hz: float | None = None, voices: dict[str, str] | None = None) -> None:
        self._pitch = speaker_pitch_hz
        self._fixed = dict(voices or {})
        self._voices: dict[str, object] = {}

    def voice_id(self, language_code: str) -> str:
        if language_code in self._fixed:
            return self._fixed[language_code]
        return resolve_voice_id(language_code, self._pitch)

    def _get_voice(self, language_code: str):
        if language_code in self._voices:
            return self._voices[language_code]

        from piper import PiperVoice

        voice = PiperVoice.load(str(_ensure_voice_downloaded(self.voice_id(language_code))))
        self._voices[language_code] = voice
        return voice

    def preload(self, language_code: str) -> None:
        self._get_voice(language_code)

    def synthesize(self, text: str, language_code: str) -> tuple[np.ndarray, int]:
        voice = self._get_voice(language_code)
        if not text.strip():
            return np.array([], dtype=np.float32), voice.config.sample_rate
        try:
            pcm_bytes = b"".join(chunk.audio_int16_bytes for chunk in voice.synthesize(text))
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                f"La voz de Piper para '{language_code}' necesita un paquete extra de Python: "
                f"instálalo con `pip install {exc.name}`."
            ) from exc
        pcm = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        return pcm, voice.config.sample_rate

    def calibration_audio(self, language_code: str) -> tuple[np.ndarray, int]:
        """La voz leyendo una frase fija, para medir su timbre."""
        return self.synthesize(_CALIBRATION.get(language_code, _CALIBRATION["en"]), language_code)
