"""Arma la versión portable de clonavoz para Windows de 64 bits (sin GPU).

El resultado es una carpeta `clonavoz-portable` que funciona en cualquier
Windows 10/11 sin instalar nada (ni Python): se puede llevar en un pendrive.
Adentro van Python "embebible" oficial, todas las dependencias (con PyTorch
para CPU), las DLLs de Visual C++ que necesita PyTorch, los lanzadores
(`Iniciar.bat`, `clonavoz.bat`) y la carpeta `datos`, donde quedan tu muestra
de voz y los modelos: nada se guarda en la computadora donde lo ejecutas.

Uso (desde la raíz del repositorio, con Python 3.10+ y pip):

    python portable/build_windows.py                    # arma dist/clonavoz-portable
    python portable/build_windows.py --models es en     # además descarga los modelos
    python portable/build_windows.py --zip              # y lo comprime

Se puede correr en Linux o macOS para armar el paquete desde otro sistema
(se bajan los paquetes de Windows directamente), pero las DLLs de Visual C++
y la descarga de modelos (que usa el Python portable) solo se hacen en Windows.
"""
from __future__ import annotations

import argparse
import io
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

PYTHON_VERSION = "3.12.10"
EMBED_URL = f"https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip"
TORCH_CPU_INDEX = "https://download.pytorch.org/whl/cpu"
# Runtime de Visual C++ que usan torch y otras librerías. Microsoft permite
# distribuirlas junto a la aplicación; así funciona aunque la PC no tenga
# instalado el "Visual C++ Redistributable".
VC_RUNTIME_DLLS = [
    "msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll", "msvcp140_atomic_wait.dll",
    "vcruntime140.dll", "vcruntime140_1.dll", "concrt140.dll",
]

ROOT = Path(__file__).resolve().parents[1]
LAUNCHERS = Path(__file__).resolve().parent / "windows"


def _run(cmd: list[str], **kwargs) -> None:
    print("$", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run(cmd, check=True, **kwargs)


def _install_python(python_dir: Path) -> None:
    print(f"Descargando Python {PYTHON_VERSION} embebible...", flush=True)
    with urllib.request.urlopen(EMBED_URL) as response:
        zipfile.ZipFile(io.BytesIO(response.read())).extractall(python_dir)
    # El archivo ._pth fija sys.path: se agrega site-packages y se habilita `site`.
    pth = next(python_dir.glob("python3*._pth"))
    stdlib_zip = next(python_dir.glob("python3*.zip")).name
    pth.write_text(f"{stdlib_zip}\n.\nLib\\site-packages\nimport site\n", encoding="ascii")


def _install_packages(site_packages: Path) -> None:
    with tempfile.TemporaryDirectory() as wheel_dir:
        _run([sys.executable, "-m", "pip", "wheel", "--no-deps", "--wheel-dir", wheel_dir, str(ROOT)])
        clonavoz_wheel = next(Path(wheel_dir).glob("clonavoz-*.whl"))
        cmd = [
            sys.executable, "-m", "pip", "install", "--target", str(site_packages),
            "--platform", "win_amd64", "--python-version", PYTHON_VERSION.rsplit(".", 1)[0],
            "--implementation", "cp", "--only-binary=:all:",
            "--index-url", TORCH_CPU_INDEX, "--extra-index-url", "https://pypi.org/simple",
            "torch", str(clonavoz_wheel),
        ]
        if sys.version_info[:2] != tuple(int(x) for x in PYTHON_VERSION.split(".")[:2]):
            cmd.append("--no-compile")  # los .pyc de otra versión de Python no sirven
        _run(cmd)


def _copy_vc_runtime(python_dir: Path) -> None:
    system32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    for name in VC_RUNTIME_DLLS:
        source = system32 / name
        if source.exists() and not (python_dir / name).exists():
            shutil.copy2(source, python_dir / name)
            print(f"  + {name}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", default=str(ROOT / "dist"), help="Carpeta donde se arma (por defecto: dist)")
    parser.add_argument(
        "--models", nargs="*", metavar="IDIOMA",
        help="Descargar ya los modelos, con las voces de estos idiomas (ej: es en). Solo en Windows.",
    )
    parser.add_argument("--zip", action="store_true", help="Comprimir el resultado en un .zip")
    args = parser.parse_args()

    target = Path(args.out) / "clonavoz-portable"
    if target.exists():
        shutil.rmtree(target)
    python_dir = target / "python"
    _install_python(python_dir)
    _install_packages(python_dir / "Lib" / "site-packages")

    on_windows = sys.platform == "win32"
    if on_windows:
        print("Copiando el runtime de Visual C++...", flush=True)
        _copy_vc_runtime(python_dir)
    else:
        print("AVISO: fuera de Windows no se copian las DLLs de Visual C++ (la PC que lo use "
              "necesitará tener instalado el Visual C++ Redistributable).")

    for launcher in LAUNCHERS.iterdir():
        shutil.copy2(launcher, target / launcher.name)
    datos = target / "datos"
    datos.mkdir(exist_ok=True)
    (datos / "NO_BORRAR.txt").write_text(
        "Aquí se guardan tu muestra de voz, los modelos descargados y los idiomas elegidos.\r\n",
        encoding="utf-8-sig",
        newline="",
    )

    if args.models is not None:
        if not on_windows:
            sys.exit("--models solo se puede usar en Windows (usa el Python portable para descargarlos).")
        _run([str(target / "clonavoz.bat"), "download-models", "--languages", *(args.models or ["es", "en"])])

    if args.zip:
        archive = shutil.make_archive(str(Path(args.out) / "clonavoz-portable-windows-x64"), "zip", args.out,
                                      "clonavoz-portable")
        print(f"Listo: {archive}")
    else:
        print(f"Listo: {target}")


if __name__ == "__main__":
    main()
