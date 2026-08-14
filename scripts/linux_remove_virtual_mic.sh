#!/usr/bin/env bash
# Elimina el micrófono virtual creado por linux_create_virtual_mic.sh
set -euo pipefail

MODULE_ID=$(pactl list short modules | grep "sink_name=clonavoz_mic" | awk '{print $1}' || true)

if [ -n "${MODULE_ID}" ]; then
    pactl unload-module "${MODULE_ID}"
    echo "Micrófono virtual eliminado."
else
    echo "No se encontró el micrófono virtual activo."
fi
