"""Detección de hardware y selección automática de perfil de rendimiento.

La idea es que el mismo programa se ajuste solo: en una notebook gamer con
GPU aprovecha CUDA y modelos más grandes/precisos, y en una laptop de
bajos recursos usa modelos livianos en CPU para poder mantenerse en tiempo
real sin trabarse. Sin GPU, la voz clonada sale del motor liviano
(Piper + OpenVoice, ver voice_clone.py): XTTS-v2 en CPU tarda varios
segundos por frase, y en una computadora modesta, mucho más.
"""
from __future__ import annotations

import dataclasses

import psutil
import torch

# Modelo de clonación del motor "xtts" (solo tiene sentido con GPU).
XTTS_MODEL = "tts_models/multilingual/multi-dataset/xtts_v2"


@dataclasses.dataclass(frozen=True)
class PerformanceProfile:
    name: str
    whisper_model: str
    whisper_compute_type: str
    tts_model: str
    max_utterance_seconds: float
    device: str
    voice_engine: str  # "openvoice" (liviano) o "xtts" (necesita GPU para ir rápido)


PROFILES: dict[str, PerformanceProfile] = {
    "low": PerformanceProfile(
        name="low",
        whisper_model="tiny",
        whisper_compute_type="int8",
        tts_model=XTTS_MODEL,
        max_utterance_seconds=3.0,
        device="cpu",
        voice_engine="openvoice",
    ),
    "medium": PerformanceProfile(
        name="medium",
        whisper_model="small",
        whisper_compute_type="int8",
        tts_model=XTTS_MODEL,
        max_utterance_seconds=5.0,
        device="cpu",
        voice_engine="openvoice",
    ),
    "high": PerformanceProfile(
        name="high",
        whisper_model="medium",
        whisper_compute_type="float16",
        tts_model=XTTS_MODEL,
        max_utterance_seconds=8.0,
        device="cuda",
        voice_engine="xtts",
    ),
}


def detect_accelerator() -> str | None:
    """Devuelve 'cuda', 'mps' (Apple Silicon) o None si no hay GPU utilizable."""
    if torch.cuda.is_available():
        return "cuda"
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return "mps"
    return None


def autodetect_profile() -> PerformanceProfile:
    accelerator = detect_accelerator()
    ram_gb = psutil.virtual_memory().total / (1024**3)
    cpu_cores = psutil.cpu_count(logical=False) or psutil.cpu_count() or 2

    if accelerator == "cuda":
        vram_gb = torch.cuda.get_device_properties(0).total_memory / (1024**3)
        base = PROFILES["high"] if vram_gb >= 6 else PROFILES["medium"]
        return dataclasses.replace(base, device="cuda")

    if accelerator == "mps":
        # Apple Silicon: buen rendimiento en CPU/GPU unificada, perfil medio.
        return dataclasses.replace(PROFILES["medium"], device="mps")

    if ram_gb >= 16 and cpu_cores >= 8:
        return PROFILES["medium"]

    return PROFILES["low"]


def get_profile(name: str | None) -> PerformanceProfile:
    """`name` puede ser None/'auto' (detección automática) o 'low'/'medium'/'high'
    para forzar un perfil manualmente."""
    if name is None or name == "auto":
        return autodetect_profile()
    if name not in PROFILES:
        raise ValueError(
            f"Perfil desconocido: '{name}'. Opciones: auto, {', '.join(PROFILES)}"
        )

    profile = PROFILES[name]
    if profile.device != "cpu":
        accelerator = detect_accelerator()
        if accelerator is None:
            print(
                f"[clonavoz] Aviso: el perfil '{name}' pide GPU pero no se detectó "
                "ninguna disponible. Usando CPU (será más lento) y el motor de voz liviano."
            )
            profile = dataclasses.replace(profile, device="cpu", voice_engine="openvoice")
        else:
            # XTTS solo es rápido con CUDA; con Apple Silicon (mps) va el motor liviano.
            engine = profile.voice_engine if accelerator == "cuda" else "openvoice"
            profile = dataclasses.replace(profile, device=accelerator, voice_engine=engine)
    return profile
