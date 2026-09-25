"""Interfaz de línea de comandos de clonavoz."""
from __future__ import annotations

import argparse
import dataclasses
import platform
import queue
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf

from . import audio_devices
from .audio_io import AudioDeviceError, AudioOutput, MicrophoneStream, beep_signal, diagnose_microphone, to_dbfs
from .console import StatusLine, format_meter
from .enroll import record_voice_sample
from .languages import get_language, list_languages
from .paths import data_dir

DEFAULT_VOICE_SAMPLE = data_dir() / "mi_voz.wav"
_WINDOWS = platform.system() == "Windows"


def _cmd_devices(_args: argparse.Namespace) -> None:
    audio_devices.print_devices()
    virtual = audio_devices.find_virtual_output_device()
    if virtual:
        print(f"\nMicrófono virtual detectado: clonavoz reproduce la traducción en [{virtual.index}] {virtual.name}")
        mic_name = audio_devices.virtual_mic_name(virtual.name)
        if mic_name:
            print(f"En Zoom/Meet/Teams/Discord elegí como micrófono: {mic_name}")
    else:
        print(
            "\nNo se detectó un micrófono virtual instalado todavía.\n"
            "Revisa el README para instalar VB-CABLE (Windows), BlackHole (macOS) "
            "o crear un sink virtual (Linux, ver scripts/linux_create_virtual_mic.sh)."
        )
    if _WINDOWS:
        print(
            "\nEn Windows cada dispositivo aparece varias veces (MME, DirectSound, WASAPI, WDM-KS): "
            "cualquiera sirve; ante la duda, usá el de MME (los primeros de la lista)."
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
    try:
        record_voice_sample(out_path, seconds=args.seconds, input_device=args.input_device)
    except AudioDeviceError as exc:
        print(exc, file=sys.stderr)
        sys.exit(1)
    print(f"Muestra de voz guardada en: {out_path}")


def _cmd_download_models(args: argparse.Namespace) -> None:
    languages = []
    for code in args.languages:
        try:
            languages.append(get_language(code))
        except ValueError as exc:
            print(exc, file=sys.stderr)
            sys.exit(1)

    from . import openvoice, piper_tts, translate  # translate carga torch antes que ctranslate2

    from faster_whisper.utils import download_model

    for size in args.whisper:
        print(f"Reconocimiento de voz (Whisper '{size}')...")
        download_model(size)
    print("Traductor (NLLB-200)...")
    translate.download()
    print("Conversor de timbre (OpenVoice V2)...")
    openvoice.ToneColorConverter.from_pretrained()
    for lang in languages:
        print(f"Voces base de Piper para {lang.name}...")
        try:
            piper_tts.download_voices(lang.code)
        except RuntimeError as exc:
            print(exc, file=sys.stderr)
            sys.exit(1)
    (data_dir() / "modelos_listos.txt").write_text(
        "Modelos descargados para: " + ", ".join(lang.code for lang in languages) + "\n", encoding="utf-8"
    )
    print(f"Listo: ya se puede usar sin internet (datos en {data_dir()}).")


def _resolve_output_device(requested: int | None, to_speakers: bool = False) -> audio_devices.AudioDevice:
    if to_speakers:
        speakers = audio_devices.default_output_device()
        if speakers is None:
            print("No se encontró una salida de audio (parlantes o auriculares).", file=sys.stderr)
            sys.exit(1)
        print(
            f"La traducción va a sonar en [{speakers.index}] {speakers.name}. Usá auriculares: si "
            "suena por parlantes, el micrófono la vuelve a escuchar y la traduce de nuevo."
        )
        return speakers
    if requested is not None:
        devices = audio_devices.list_devices()
        if 0 <= requested < len(devices) and devices[requested].max_output_channels > 0:
            return devices[requested]
        print(
            f"--output-device {requested} no es un dispositivo de salida. "
            "Ejecuta `clonavoz devices` para ver la lista.",
            file=sys.stderr,
        )
        sys.exit(1)
    virtual = audio_devices.find_virtual_output_device()
    if virtual is None:
        print(
            "No se especificó --output-device y no se detectó un micrófono "
            "virtual instalado. Ejecuta `clonavoz devices` para ver la lista, "
            "o revisa el README para instalar uno.",
            file=sys.stderr,
        )
        sys.exit(1)
    print(f"Salida detectada automáticamente: [{virtual.index}] {virtual.name}")
    return virtual


def _check_voice_sample(path: Path) -> None:
    if not path.exists():
        print(
            f"No se encontró la muestra de voz de referencia: {path}\nGrábala primero con `clonavoz enroll`.",
            file=sys.stderr,
        )
        sys.exit(1)
    audio, _rate = sf.read(str(path), dtype="float32")
    peak_db = to_dbfs(float(np.max(np.abs(audio))) if audio.size else 0.0)
    if peak_db < -40:
        print(
            f"Aviso: tu muestra de voz ({path}) está casi en silencio (máximo {peak_db:.0f} dB), así que "
            "la voz clonada va a salir mal o muda. Revisá el micrófono con `clonavoz test-audio` y "
            "volvé a grabarla con `clonavoz enroll`."
        )


def _cmd_run(args: argparse.Namespace) -> None:
    try:
        get_language(args.source_lang)
        get_language(args.target_lang)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        sys.exit(1)

    # Importes pesados (torch, Whisper, NLLB, voz) solo para este comando.
    from .config import get_profile
    from .pipeline import LiveVoicePipeline
    from .voice_clone import xtts_installed

    profile = get_profile(args.profile)
    engine = profile.voice_engine if args.voice_engine == "auto" else args.voice_engine
    if engine == "xtts" and not xtts_installed():
        if args.voice_engine == "xtts":
            print('El motor XTTS no está instalado. Instálalo con: pip install "coqui-tts[codec]"', file=sys.stderr)
            sys.exit(1)
        engine = "openvoice"
    profile = dataclasses.replace(profile, voice_engine=engine)
    engine_name = "liviano (Piper + OpenVoice)" if engine == "openvoice" else "XTTS-v2"
    print(f"Perfil de rendimiento: {profile.name} (dispositivo: {profile.device}, voz: {engine_name})")

    reference_wav = Path(args.voice_sample) if args.voice_sample else DEFAULT_VOICE_SAMPLE
    _check_voice_sample(reference_wav)
    output = _resolve_output_device(args.output_device, to_speakers=args.to_speakers)
    input_device, notes = audio_devices.resolve_input_device(args.input_device)
    for note in notes:
        print(note)

    status = StatusLine()

    def on_transcript(text_src: str, text_tgt: str) -> None:
        status.print(f"  {args.source_lang} > {text_src}")
        status.print(f"  {args.target_lang} > {text_tgt}")

    print("Cargando modelos (la primera vez se descargan y puede tardar varios minutos)...")
    try:
        pipeline = LiveVoicePipeline(
            profile=profile,
            source_lang=args.source_lang,
            target_lang=args.target_lang,
            reference_wav=reference_wav,
            input_device=input_device,
            output_device=output.index,
            on_transcript=on_transcript,
            on_message=status.print,
        )
    except RuntimeError as exc:  # ej. idioma sin voz de Piper, o le falta un paquete
        print(exc, file=sys.stderr)
        sys.exit(1)
    try:
        pipeline.start()
    except AudioDeviceError as exc:
        print(exc, file=sys.stderr)
        sys.exit(1)

    print(f"Micrófono (tu voz): {pipeline.mic.description}")
    print(f"Salida de la traducción: {pipeline.output.description}")
    mic_name = audio_devices.virtual_mic_name(output.name)
    if mic_name:
        print(f"En Zoom/Meet/Teams/Discord elegí como micrófono: {mic_name}")
    print(
        "Escuchando... hablá normalmente: cada frase sale traducida por el micrófono virtual "
        "unos segundos después de que la terminás. Ctrl+C para detener."
    )
    try:
        _show_live_status(pipeline, status)
    except KeyboardInterrupt:
        pass
    finally:
        status.clear()
        pipeline.stop()
    if pipeline.error is not None:
        print(f"Error: {pipeline.error}", file=sys.stderr)
        sys.exit(1)


def _show_live_status(pipeline, status: StatusLine) -> None:
    """Medidor del micrófono + en qué está el pipeline, hasta Ctrl+C o un
    error. Avisa si del micrófono no llega audio (en vez de quedarse mudo)."""
    started = time.monotonic()
    checked_mic = False
    warned_stall = False
    while pipeline.error is None:
        live = pipeline.poll_status()
        state = []
        if live.speaking:
            state.append("hablando")
        if live.pending:
            state.append(f"traduciendo {live.pending}")
        if live.playing:
            state.append("reproduciendo")
        status.update(f"Mic {format_meter(live.level_db, width=16)} | {', '.join(state) or 'esperando voz'}")

        now = time.monotonic()
        stats = pipeline.mic.stats
        if not checked_mic and now - started > 5:
            checked_mic = True
            for problem in diagnose_microphone(stats, windows=_WINDOWS, expect_voice=False):
                status.print(f"[clonavoz] Aviso: {problem}")
        if checked_mic and stats.blocks and not warned_stall and now - stats.last_block_time > 3:
            warned_stall = True
            status.print("[clonavoz] Aviso: el micrófono dejó de entregar audio (¿se desconectó?).")
        time.sleep(0.1)


def _cmd_test_audio(args: argparse.Namespace) -> None:
    print("== 1/2: tu micrófono ==")
    device, notes = audio_devices.resolve_input_device(args.input_device)
    for note in notes:
        print(note)
    vad = None
    try:
        from .vad import StreamingVAD

        vad = StreamingVAD()
    except Exception as exc:  # noqa: BLE001 - la prueba de nivel sirve igual sin detector de voz
        print(f"(No se pudo cargar el detector de voz; se mide solo el nivel: {exc})")

    frames: queue.Queue = queue.Queue()
    mic = MicrophoneStream(device, on_frame=frames.put if vad is not None else None)
    try:
        mic.start()
    except AudioDeviceError as exc:
        print(exc, file=sys.stderr)
        sys.exit(1)
    print(f"Micrófono: {mic.description}")
    print(f"Hablá durante {args.seconds:.0f} segundos: el medidor tiene que moverse con tu voz.")

    status = StatusLine()
    speech_frames = 0
    end = time.monotonic() + args.seconds
    try:
        while time.monotonic() < end:
            while vad is not None and not frames.empty():
                vad.push(frames.get_nowait())
                speech_frames += vad.is_speaking
            voice = " | VOZ DETECTADA" if vad is not None and vad.is_speaking else ""
            status.update(f"Mic {format_meter(mic.pop_level_db())}{voice}")
            time.sleep(0.05)
    finally:
        mic.close()
        status.clear()

    summary = f"Nivel máximo: {to_dbfs(mic.stats.max_peak):.0f} dB"
    if vad is not None:
        summary += f" | voz detectada durante {speech_frames * vad.frame_size() / audio_devices.SAMPLE_RATE:.1f} s"
    print(summary)
    heard_speech = speech_frames > 0 if vad is not None else None
    problems = diagnose_microphone(mic.stats, heard_speech, windows=_WINDOWS)
    for problem in problems:
        print(f"PROBLEMA: {problem}")
    if not problems:
        print("OK: clonavoz escucha bien tu micrófono.")

    if not args.skip_output:
        print("\n== 2/2: micrófono virtual (lo que escucha la videollamada) ==")
        problems += _test_virtual_mic(args.output_device)
    if problems:
        sys.exit(1)


def _test_virtual_mic(requested_output: int | None) -> list[str]:
    output = _resolve_output_device(requested_output)
    player = AudioOutput(output.index)
    try:
        player.start()
    except AudioDeviceError as exc:
        print(f"PROBLEMA: {exc}")
        return [str(exc)]

    # Si se encuentra la otra punta del cable (ej. "CABLE Output"), se graba
    # mientras suenan los pitidos para comprobar solos que el audio llega.
    loopback = None
    loopback_device = audio_devices.find_virtual_mic_input(output)
    if loopback_device is not None:
        loopback = MicrophoneStream(loopback_device.index)
        try:
            loopback.start()
        except AudioDeviceError:
            loopback = None

    mic_name = audio_devices.virtual_mic_name(output.name)
    print(f"Reproduciendo 3 pitidos en {player.description}...")
    if mic_name:
        print(
            f"Mientras suenan, el medidor de '{mic_name}' (en la configuración de sonido, o en la "
            "prueba de micrófono de Zoom/Meet/Discord) tiene que moverse."
        )
    try:
        player.play(beep_signal(player.samplerate), player.samplerate)
    finally:
        player.close()  # espera a que termine de sonar
        if loopback is not None:
            time.sleep(0.3)
            loopback.close()

    if loopback is None:
        return []
    if to_dbfs(loopback.stats.max_peak) > -40:
        print(f"OK: los pitidos llegaron a {loopback.description}. Ese es el micrófono a elegir en la videollamada.")
        return []
    problem = (
        f"los pitidos NO llegaron a {loopback.description}. Revisá que el cable virtual esté bien "
        "instalado (en Windows, reiniciá después de instalar VB-CABLE) y que ese dispositivo no "
        "esté deshabilitado ni silenciado en la configuración de sonido."
    )
    print(f"PROBLEMA: {problem}")
    return [problem]


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

    p_test = sub.add_parser(
        "test-audio",
        help="Prueba tu micrófono (medidor de nivel) y el micrófono virtual (pitidos de prueba)",
    )
    p_test.add_argument("--seconds", type=float, default=8.0, help="Segundos para probar el micrófono")
    p_test.add_argument("--input-device", type=int, default=None, help="Índice del micrófono de entrada")
    p_test.add_argument("--output-device", type=int, default=None, help="Índice del micrófono virtual de salida")
    p_test.add_argument("--skip-output", action="store_true", help="Probar solo el micrófono")
    p_test.set_defaults(func=_cmd_test_audio)

    p_enroll = sub.add_parser("enroll", help="Graba una muestra de tu voz para clonarla")
    p_enroll.add_argument("--output", help="Ruta donde guardar el .wav de referencia")
    p_enroll.add_argument("--seconds", type=float, default=15.0)
    p_enroll.add_argument("--input-device", type=int, default=None)
    p_enroll.set_defaults(func=_cmd_enroll)

    p_download = sub.add_parser(
        "download-models", help="Descarga todo lo necesario para usar clonavoz sin internet"
    )
    p_download.add_argument(
        "--languages", nargs="+", default=["es", "en"],
        help="Idiomas que vas a hacer escuchar (sus voces base), ej: en pt fr. Por defecto: es en",
    )
    p_download.add_argument(
        "--whisper", nargs="+", default=["tiny", "small"],
        help="Modelos de reconocimiento de voz: tiny (perfil low) y small (perfil medium)",
    )
    p_download.set_defaults(func=_cmd_download_models)

    p_run = sub.add_parser("run", help="Inicia la traducción de voz en vivo")
    p_run.add_argument("--source-lang", required=True, help="Idioma en el que hablas, ej: es")
    p_run.add_argument("--target-lang", required=True, help="Idioma que escucharán, ej: en")
    p_run.add_argument("--voice-sample", help="Ruta al .wav de referencia de tu voz")
    p_run.add_argument("--input-device", type=int, default=None, help="Índice del micrófono de entrada")
    p_run.add_argument("--output-device", type=int, default=None, help="Índice del micrófono virtual de salida")
    p_run.add_argument(
        "--to-speakers",
        action="store_true",
        help="Escuchar la traducción en tus auriculares en vez de mandarla a una videollamada "
        "(no necesita micrófono virtual)",
    )
    p_run.add_argument(
        "--profile",
        default="auto",
        choices=["auto", "low", "medium", "high"],
        help="Perfil de rendimiento. 'auto' detecta tu hardware.",
    )
    p_run.add_argument(
        "--voice-engine",
        default="auto",
        choices=["auto", "openvoice", "xtts"],
        help="Cómo se genera tu voz: 'openvoice' (liviano y rápido, por defecto sin GPU) o "
        "'xtts' (XTTS-v2, más pesado; por defecto con GPU NVIDIA).",
    )
    p_run.set_defaults(func=_cmd_run)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
