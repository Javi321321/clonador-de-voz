"""Voz natural con Pocket TTS (Kyutai, 2026): un modelo chico (100 millones de
parámetros) que corre en el procesador, más rápido que tiempo real con 1 o 2
núcleos, y que clona tu voz directamente desde tu muestra: la frase se genera
ya con tu voz, en vez de decirla una voz base a la que después se le cambia
el timbre (lo que hace el motor liviano, Piper + OpenVoice).

En nuestras pruebas (5 personas reales, hablando inglés con su voz clonada de
una muestra en español) se pareció más a la persona real (0.93 contra 0.84
del motor liviano, donde otra grabación de la misma persona da 0.98), sonó
más natural y tardó menos. Además entrega el audio a medida que lo genera:
la traducción empieza a sonar ~0.1 s después de empezar a generarla.

Idiomas en los que habla: inglés, español, francés, alemán, portugués,
italiano y neerlandés. Para los demás se usa el motor liviano.

En procesadores con AVX2 (casi todos desde 2013) se usa la versión int8 del
modelo: en nuestras pruebas se pareció igual a la persona (0.928 contra
0.929) y sonó igual de natural, pero tarda ~25% menos. Los Celeron/Pentium
más baratos no tienen AVX2: ahí se usa la versión normal.

Los pesos del modelo que permite clonar voces están en Hugging Face con una
condición: aceptar que solo vas a clonar una voz con el consentimiento de su
dueño (la tuya). Se acepta una vez, con una cuenta gratis, en
https://huggingface.co/kyutai/pocket-tts, y `clonavoz download-models
--hf-token ...` los descarga. Después funciona sin internet y sin cuenta.

Licencias: código MIT; pesos CC-BY-4.0, © Kyutai (https://kyutai.org).
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
import platform
import re
import warnings
from collections.abc import Iterator
from pathlib import Path

import numpy as np

from .paths import data_dir

# Código de idioma de clonavoz -> modelo de Pocket TTS.
LANGUAGES = {
    "en": "english",
    "es": "spanish",
    "fr": "french",
    "de": "german",
    "pt": "portuguese",
    "it": "italian",
    "nl": "dutch",
}
TERMS_URL = "https://huggingface.co/kyutai/pocket-tts"
TOKENS_URL = "https://huggingface.co/settings/tokens"
# Con 2 hilos es lo más rápido en nuestras pruebas (con 4 tarda más).
_THREADS = 2


def supports(language_code: str) -> bool:
    return language_code in LANGUAGES


def load_model(int8: bool, **kwargs):
    """El modelo de Pocket TTS (`kwargs` como `TTSModel.load_model`), normal o int8."""
    TTSModel, _ = _import_pocket()
    if not int8:
        return TTSModel.load_model(**kwargs)
    with warnings.catch_warnings():
        # PyTorch avisa que su cuantización int8 va a cambiar de lugar (según la
        # versión, como DeprecationWarning o UserWarning): no afecta en nada.
        warnings.simplefilter("ignore")
        return TTSModel.load_model(quantize=True, **kwargs)


def int8_supported() -> bool:
    """Si este procesador puede usar la versión int8 (la de PyTorch necesita AVX2)."""
    if platform.machine().lower() not in ("amd64", "x86_64"):
        return False
    import torch

    return torch.backends.cpu.get_cpu_capability() in ("AVX2", "AVX512")


def _import_pocket():
    import torch

    threads = torch.get_num_threads()
    from pocket_tts import TTSModel, export_model_state

    # pocket_tts deja a torch con 1 hilo al importarse (para todo el proceso).
    torch.set_num_threads(max(1, min(_THREADS, threads)))
    return TTSModel, export_model_state


def _config_path(language_code: str) -> Path:
    """La configuración del modelo de ese idioma. CLONAVOZ_POCKET_CONFIG_DIR
    permite reemplazarla por otra (para probar otros pesos)."""
    name = LANGUAGES[language_code]
    override = os.environ.get("CLONAVOZ_POCKET_CONFIG_DIR")
    if override and (Path(override) / f"{name}.yaml").exists():
        return Path(override) / f"{name}.yaml"
    # Se busca sin importar pocket_tts (importarlo cambia los hilos de torch).
    spec = importlib.util.find_spec("pocket_tts")
    if spec is None or not spec.submodule_search_locations:
        raise RuntimeError("Falta el paquete de la voz natural: pip install pocket-tts")
    return Path(list(spec.submodule_search_locations)[0]) / "config" / f"{name}.yaml"


def _cloning_weights(language_code: str) -> tuple[str, str, str | None]:
    """(repo, archivo, revisión) de los pesos que permiten clonar voces."""
    text = _config_path(language_code).read_text(encoding="utf-8")
    match = re.search(r"^weights_path:\s*hf://([^/\s]+/[^/\s]+)/(\S+)$", text, flags=re.M)
    if match is None:
        raise RuntimeError(f"No se entiende la configuración de Pocket TTS de '{language_code}'.")
    path, _, revision = match.group(2).partition("@")
    return match.group(1), path, revision or None


def cloning_ready(language_code: str) -> bool:
    """True si ya están descargados los pesos para clonar tu voz en ese idioma
    (no usa internet)."""
    if not supports(language_code):
        return False
    try:
        from huggingface_hub import try_to_load_from_cache

        repo, filename, revision = _cloning_weights(language_code)
        cached = try_to_load_from_cache(repo, filename, revision=revision)
    except Exception:  # noqa: BLE001 - ante cualquier duda, se usa el motor liviano
        return False
    return isinstance(cached, str) and Path(cached).exists()


def download(language_code: str, token: str | None = None) -> None:
    """Descarga el modelo de ese idioma con clonación de voz. Sin acceso (falta
    aceptar las condiciones o el token), lanza RuntimeError explicando qué hacer."""
    from huggingface_hub import hf_hub_download
    from huggingface_hub.utils import GatedRepoError, HfHubHTTPError

    repo, filename, revision = _cloning_weights(language_code)
    try:
        hf_hub_download(repo, filename, revision=revision, token=token)
    except GatedRepoError as exc:
        raise RuntimeError(
            "Para la voz natural falta un paso, una sola vez:\n"
            f"  1. Entrá a {TERMS_URL} con tu cuenta gratis de Hugging Face y aceptá las condiciones\n"
            "     (clonar solo voces con el permiso de su dueño: la tuya).\n"
            f"  2. Creá un token de lectura (Read) en {TOKENS_URL}\n"
            "  3. Volvé a descargar los modelos pegando ese token."
            + ("" if token else "\n(No se usó ningún token.)")
        ) from exc
    except HfHubHTTPError as exc:
        raise RuntimeError(f"No se pudo descargar la voz natural ({exc}). Revisá la conexión y el token.") from exc
    # El resto (tokenizador, etc.) se baja al cargar el modelo.
    TTSModel, _ = _import_pocket()
    model = TTSModel.load_model(config=str(_config_path(language_code)))
    if not model.has_voice_cloning:
        raise RuntimeError("Se descargó el modelo, pero sin la parte que clona voces. Probá de nuevo.")


class PocketVoice:
    """Tu voz clonada con Pocket TTS, en los idiomas que ya estén descargados.
    `int8`: usar la versión int8 (None = si el procesador la soporta)."""

    def __init__(self, reference_wav: Path, int8: bool | None = None) -> None:
        self.reference_wav = Path(reference_wav)
        self.int8 = int8_supported() if int8 is None else int8
        self._models: dict[str, object] = {}
        self._states: dict[str, object] = {}
        self.sample_rate = 24000

    def _load(self, language_code: str):
        config = str(_config_path(language_code))
        if self.int8:
            try:
                return load_model(True, config=config)
            except Exception:  # noqa: BLE001 - sin int8 (ej. otra versión de PyTorch): la normal
                self.int8 = False
        return load_model(False, config=config)

    def _model(self, language_code: str):
        if language_code not in self._models:
            model = self._load(language_code)
            self.sample_rate = model.sample_rate
            self._models[language_code] = model
        return self._models[language_code]

    def _digest(self) -> str:
        return hashlib.sha1(self.reference_wav.read_bytes()).hexdigest()[:12]

    def _state_file(self, language_code: str) -> Path:
        variant = "_int8" if self.int8 else ""
        return data_dir() / "voces" / f"pocket_{LANGUAGES[language_code]}_{self._digest()}{variant}.safetensors"

    def _state(self, language_code: str):
        """Tu voz "cargada" en el modelo. Calcularla tarda 1-3 s, así que se guarda
        en la carpeta de datos y se vuelve a calcular solo si cambia tu muestra."""
        if language_code not in self._states:
            model = self._model(language_code)
            path = self._state_file(language_code)
            if path.exists():
                state = model.get_state_for_audio_prompt(path)
            else:
                if not model.has_voice_cloning:
                    raise RuntimeError(
                        "Falta descargar la voz natural (con clonación) para este idioma: "
                        "usá `clonavoz download-models` con tu token de Hugging Face."
                    )
                state = model.get_state_for_audio_prompt(self.reference_wav, truncate=True)
                _, export_model_state = _import_pocket()
                path.parent.mkdir(parents=True, exist_ok=True)
                for old in path.parent.glob(f"pocket_{LANGUAGES[language_code]}_*.safetensors"):
                    if self._digest() not in old.name:
                        old.unlink()  # la de una muestra anterior
                export_model_state(state, path)
            self._states[language_code] = state
        return self._states[language_code]

    def preload(self, language_code: str) -> None:
        self._state(language_code)
        if self.int8:
            try:
                self._model(language_code).generate_audio(self._states[language_code], "Hi.")
            except Exception:  # noqa: BLE001 - la int8 no anda en este procesador: la normal
                self.int8 = False
                self._models.pop(language_code)
                self._states.pop(language_code)
                self._state(language_code)

    def synthesize(self, text: str, language_code: str) -> tuple[np.ndarray, int]:
        audio = self._model(language_code).generate_audio(self._state(language_code), text)
        return audio.squeeze(0).numpy().astype(np.float32), self.sample_rate

    def stream(self, text: str, language_code: str) -> Iterator[np.ndarray]:
        """La frase en pedacitos, a medida que se generan (el primero sale en ~0.1 s)."""
        model, state = self._model(language_code), self._state(language_code)
        for chunk in model.generate_audio_stream(state, text):
            yield chunk.reshape(-1).numpy().astype(np.float32)
