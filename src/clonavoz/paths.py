"""Dónde guarda clonavoz tus datos: la muestra de tu voz, las voces de Piper
descargadas y los modelos convertidos. Por defecto en ~/.clonavoz.

La versión portable lo cambia con la variable de entorno CLONAVOZ_HOME (y
los cachés de modelos con HF_HOME), para que todo quede dentro de su propia
carpeta, por ejemplo en un pendrive, y nada tuyo quede en la computadora
donde la ejecutas.
"""
from __future__ import annotations

import os
from pathlib import Path


def data_dir() -> Path:
    custom = os.environ.get("CLONAVOZ_HOME")
    return Path(custom) if custom else Path.home() / ".clonavoz"
