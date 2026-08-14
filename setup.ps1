# Instalación de un solo comando para Windows: crea el entorno virtual,
# detecta si hay GPU NVIDIA para instalar el PyTorch correcto, e instala
# todas las dependencias del proyecto.
#
# Uso: abre PowerShell en esta carpeta y ejecuta:
#   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
#   .\setup.ps1

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Error "Python 3.10+ no está instalado. Instálalo desde https://python.org primero."
    exit 1
}

python -m venv .venv
& .\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip

$hasNvidia = $null -ne (Get-Command nvidia-smi -ErrorAction SilentlyContinue)
if ($hasNvidia) {
    Write-Host "GPU NVIDIA detectada: instalando PyTorch con soporte CUDA..."
    pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121
} else {
    Write-Host "Sin GPU NVIDIA detectada: instalando PyTorch para CPU..."
    pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
}

pip install -r requirements.txt
pip install -e .

Write-Host ""
Write-Host "Listo. En cada terminal nueva, activa el entorno con:"
Write-Host "  .\.venv\Scripts\Activate.ps1"
Write-Host ""
Write-Host "Luego prueba con:"
Write-Host "  clonavoz devices"
Write-Host "  clonavoz enroll --seconds 15"
Write-Host "  clonavoz run --source-lang es --target-lang en"
Write-Host ""
Write-Host "Antes de 'run', instala VB-CABLE si no lo tienes: https://vb-audio.com/Cable/"
