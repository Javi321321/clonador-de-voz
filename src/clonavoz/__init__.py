"""clonavoz: traductor de voz en vivo con clonación de tu propia voz, 100% local."""
import os

# 100% local: sin telemetría de las librerías que la traen activada por defecto.
# onnxruntime (lo usa Piper) además guarda un identificador de equipo en la
# carpeta del usuario, que quedaría en cualquier PC donde uses la versión portable.
os.environ.setdefault("ORT_DISABLE_TELEMETRY", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
# Los modelos se bajan por HTTP común; sin esto, huggingface_hub avisa en cada
# archivo que existe un sistema de descarga más nuevo (hf_xet) que no se usa.
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

__version__ = "0.1.0"
