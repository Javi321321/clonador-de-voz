"""TTS de respaldo (sin clonación de voz) para idiomas que XTTS-v2 todavía
no soporta, usando Piper: un motor muy liviano basado en ONNX, ideal para
equipos de bajos recursos. Las voces se descargan una sola vez (necesitas
internet la primera vez que uses un idioma nuevo) y después quedan en
caché local para funcionar 100% sin conexión.

Nota: esta es la pieza más nueva/menos probada del proyecto porque depende
de la API de la librería `piper-tts`, que cambia entre versiones. Si algo
falla, revisa la versión instalada (`pip show piper-tts`) contra
https://github.com/rhasspy/piper — el mecanismo de descarga de voces
(`python -m piper.download_voices`) es estable, pero la clase de síntesis
puede requerir ajustes menores según la versión.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np

_VOICES_DIR = Path.home() / ".clonavoz" / "piper_voices"

# IDs de voces del catálogo público de Piper
# (https://huggingface.co/rhasspy/piper-voices). Puedes agregar/editar
# idiomas creando ~/.clonavoz/piper_voices.json, ej:
#   {"bn": "bn_BD-nombre_de_la_voz-medium"}
_DEFAULT_VOICES = {
    "uk": "uk_UA-ukrainian_tts-medium",
    "sv": "sv_SE-nst-medium",
    "el": "el_GR-rapunzelina-low",
    "ro": "ro_RO-mihai-medium",
    "vi": "vi_VN-vivos-x_low",
    "id": "id_ID-fajri-medium",
    "fi": "fi_FI-harri-medium",
    "da": "da_DK-talesyntese-medium",
    "no": "no_NO-talesyntese-medium",
}


def _user_overrides() -> dict:
    path = Path.home() / ".clonavoz" / "piper_voices.json"
    if path.exists():
        return json.loads(path.read_text())
    return {}


def resolve_voice_id(language_code: str) -> str:
    overrides = _user_overrides()
    if language_code in overrides:
        return overrides[language_code]
    if language_code in _DEFAULT_VOICES:
        return _DEFAULT_VOICES[language_code]
    raise RuntimeError(
        f"No hay una voz Piper configurada para el idioma '{language_code}'.\n"
        "Busca un modelo para ese idioma en https://huggingface.co/rhasspy/piper-voices "
        f"y agrégalo en ~/.clonavoz/piper_voices.json, por ejemplo:\n"
        f'  {{"{language_code}": "xx_XX-nombre-medium"}}'
    )


def _ensure_voice_downloaded(voice_id: str) -> Path:
    _VOICES_DIR.mkdir(parents=True, exist_ok=True)
    onnx_path = _VOICES_DIR / f"{voice_id}.onnx"
    if not onnx_path.exists():
        print(f"[clonavoz] Descargando voz Piper '{voice_id}' (una sola vez)...")
        subprocess.run(
            [sys.executable, "-m", "piper.download_voices", voice_id, "--download-dir", str(_VOICES_DIR)],
            check=True,
        )
    return onnx_path


class FallbackSynthesizer:
    """Voz neutra de alta calidad (no clonada) para idiomas fuera del
    alcance actual de la clonación XTTS-v2, así el sistema sigue
    funcionando de extremo a extremo en cualquier idioma."""

    def __init__(self) -> None:
        self._voices: dict[str, object] = {}

    def _get_voice(self, language_code: str):
        if language_code in self._voices:
            return self._voices[language_code]

        from piper import PiperVoice

        voice_id = resolve_voice_id(language_code)
        onnx_path = _ensure_voice_downloaded(voice_id)
        voice = PiperVoice.load(str(onnx_path))
        self._voices[language_code] = voice
        return voice

    def synthesize(self, text: str, language_code: str) -> tuple[np.ndarray, int]:
        if not text.strip():
            return np.array([], dtype=np.float32), 22050

        voice = self._get_voice(language_code)
        pcm_bytes = b"".join(chunk.audio_int16_bytes for chunk in voice.synthesize(text))
        pcm = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        return pcm, voice.config.sample_rate
