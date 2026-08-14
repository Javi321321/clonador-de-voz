#!/usr/bin/env bash
# Instalación de un solo comando para Linux/macOS: crea el entorno virtual,
# detecta si hay GPU NVIDIA para instalar el PyTorch correcto, e instala
# todas las dependencias del proyecto.
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
    echo "Python 3.10+ no está instalado. Instálalo primero." >&2
    exit 1
fi

python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --upgrade pip

OS_NAME=$(uname -s)

# Se fija <2.9 a propósito: desde esa versión, torchaudio necesita
# torchcodec (que a su vez necesita FFmpeg aparte). Nos quedamos en la
# rama estable anterior para evitar esa dependencia extra en todos los SO.
if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
    echo "GPU NVIDIA detectada: instalando PyTorch con soporte CUDA..."
    pip install "torch<2.9,>=2.1.0" "torchaudio<2.9,>=2.1.0" --index-url https://download.pytorch.org/whl/cu121
else
    echo "Sin GPU NVIDIA detectada: instalando PyTorch para CPU..."
    pip install "torch<2.9,>=2.1.0" "torchaudio<2.9,>=2.1.0" --index-url https://download.pytorch.org/whl/cpu
fi

pip install -r requirements.txt
pip install -e .

echo ""
echo "Listo. En cada terminal nueva, activa el entorno con:"
echo "  source .venv/bin/activate"
echo ""
echo "Luego prueba con:"
echo "  clonavoz devices"
echo "  clonavoz enroll --seconds 15"
echo "  clonavoz run --source-lang es --target-lang en"

if [ "$OS_NAME" = "Linux" ]; then
    echo ""
    echo "Antes de 'run', crea el micrófono virtual (una vez):"
    echo "  ./scripts/linux_create_virtual_mic.sh"
elif [ "$OS_NAME" = "Darwin" ]; then
    echo ""
    echo "Antes de 'run', instala BlackHole si no lo tienes:"
    echo "  brew install blackhole-2ch"
fi
