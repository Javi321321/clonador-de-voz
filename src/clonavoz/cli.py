"""Interfaz de línea de comandos de clonavoz."""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from . import audio_devices
from .config import get_profile
from .enroll import record_voice_sample
from .languages import get_language, list_languages
from .pipeline import LiveVoicePipeline

DEFAULT_VOICE_SAMPLE = Path.home() / ".clonavoz" / "mi_voz.wav"


def _cmd_devices(_args: argparse.Namespace) -> None:
    audio_devices.print_devices()
    virtual = audio_devices.find_virtual_output_device()
    if virtual:
        print(f"\nMicrófono virtual detectado: [{virtual.index}] {virtual.name}")
    else:
        print(
            "\nNo se detectó un micrófono virtual instalado todavía.\n"
            "Revisa el README para instalar VB-CABLE (Windows), BlackHole (macOS) "
            "o crear un sink virtual (Linux, ver scripts/linux_create_virtual_mic.sh)."
        )


def _cmd_languages(_args: argparse.Namespace) -> None:
    print(f"{'código':8s} {'idioma':22s} clonación de voz")
    for lang in list_languages():
        clona = "sí" if lang.xtts_code else "no (voz neutra)"
        print(f"{lang.code:8s} {lang.name:22s} {clona}")
    print(
        "\nTambién puedes usar directamente cualquier código NLLB-200 (formato "
        "FLORES-200, ej. 'ben_Beng') para traducir a otros idiomas que no están "
        "en esta lista, aunque no tengan clonación de voz."
    )


def _cmd_enroll(args: argparse.Namespace) -> None:
    out_path = Path(args.output) if args.output else DEFAULT_VOICE_SAMPLE
    out_path.parent.mkdir(parents=True, exist_ok=True)
    record_voice_sample(out_path, seconds=args.seconds, input_device=args.input_device)
    print(f"Muestra de voz guardada en: {out_path}")


def _cmd_run(args: argparse.Namespace) -> None:
    try:
        get_language(args.source_lang)
        get_language(args.target_lang)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        sys.exit(1)

    profile = get_profile(args.profile)
    print(f"Perfil de rendimiento: {profile.name} (dispositivo: {profile.device})")

    reference_wav = Path(args.voice_sample) if args.voice_sample else DEFAULT_VOICE_SAMPLE

    output_device = args.output_device
    if output_device is None:
        virtual = audio_devices.find_virtual_output_device()
        if virtual is None:
            print(
                "No se especificó --output-device y no se detectó un micrófono "
                "virtual instalado. Ejecuta `clonavoz devices` para ver la lista, "
                "o revisa el README para instalar uno.",
                file=sys.stderr,
            )
            sys.exit(1)
        output_device = virtual.index
        print(f"Salida detectada automáticamente: [{output_device}] {virtual.name}")

    def on_transcript(text_src: str, text_tgt: str) -> None:
        print(f"  {args.source_lang} > {text_src}")
        print(f"  {args.target_lang} > {text_tgt}")

    pipeline = LiveVoicePipeline(
        profile=profile,
        source_lang=args.source_lang,
        target_lang=args.target_lang,
        reference_wav=reference_wav,
        input_device=args.input_device,
        output_device=output_device,
        on_transcript=on_transcript,
    )
    pipeline.start()
    print("Escuchando... selecciona el micrófono virtual en tu app de videollamada. Ctrl+C para detener.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        pipeline.stop()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="clonavoz",
        description="Traductor de voz en vivo con clonación de tu propia voz, 100% local y gratis.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_devices = sub.add_parser("devices", help="Lista los dispositivos de audio disponibles")
    p_devices.set_defaults(func=_cmd_devices)

    p_languages = sub.add_parser("languages", help="Lista los idiomas soportados")
    p_languages.set_defaults(func=_cmd_languages)

    p_enroll = sub.add_parser("enroll", help="Graba una muestra de tu voz para clonarla")
    p_enroll.add_argument("--output", help="Ruta donde guardar el .wav de referencia")
    p_enroll.add_argument("--seconds", type=float, default=15.0)
    p_enroll.add_argument("--input-device", type=int, default=None)
    p_enroll.set_defaults(func=_cmd_enroll)

    p_run = sub.add_parser("run", help="Inicia la traducción de voz en vivo")
    p_run.add_argument("--source-lang", required=True, help="Idioma en el que hablas, ej: es")
    p_run.add_argument("--target-lang", required=True, help="Idioma que escucharán, ej: en")
    p_run.add_argument("--voice-sample", help="Ruta al .wav de referencia de tu voz")
    p_run.add_argument("--input-device", type=int, default=None, help="Índice del micrófono de entrada")
    p_run.add_argument("--output-device", type=int, default=None, help="Índice del micrófono virtual de salida")
    p_run.add_argument(
        "--profile",
        default="auto",
        choices=["auto", "low", "medium", "high"],
        help="Perfil de rendimiento. 'auto' detecta tu hardware.",
    )
    p_run.set_defaults(func=_cmd_run)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
