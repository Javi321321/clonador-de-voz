#!/usr/bin/env bash
# Crea un micrófono virtual "clonavoz" en PulseAudio/PipeWire para que
# aplicaciones como Zoom, Meet, Teams o Discord puedan usarlo como entrada.
set -euo pipefail

SINK_NAME="clonavoz_mic"
SINK_DESC="ClonaVoz_Mic"

if ! command -v pactl >/dev/null 2>&1; then
    echo "No se encontró 'pactl'. Instala PulseAudio o PipeWire-pulse primero." >&2
    exit 1
fi

if pactl list short modules | grep -q "sink_name=${SINK_NAME}"; then
    echo "El micrófono virtual '${SINK_DESC}' ya existe."
else
    pactl load-module module-null-sink sink_name="${SINK_NAME}" sink_properties=device.description="${SINK_DESC}"
    echo "Micrófono virtual '${SINK_DESC}' creado."
fi

echo ""
echo "En clonavoz, usa 'clonavoz devices' para encontrar el índice de salida"
echo "'${SINK_NAME}' (o pásalo con --output-device)."
echo ""
echo "En Zoom/Meet/Teams/Discord, selecciona '${SINK_DESC}.monitor' como micrófono de entrada."
