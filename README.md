# clonavoz

Traductor de voz **en vivo**, con **clonación de tu propia voz**, para usar dentro de
videollamadas (Zoom, Google Meet, Microsoft Teams, Discord, o cualquier otra app):
hablas en un idioma y la otra persona te escucha en el idioma que elijas, con tu mismo
tono/timbre de voz. Funciona **100% local y gratis** — sin APIs de pago, sin nube, todo
corre en tu computadora — y se adapta solo al hardware que tengas (desde una laptop
modesta hasta una notebook gamer con GPU).

## ⚠️ Expectativas realistas (leer antes de usar)

Este proyecto corre modelos de IA (reconocimiento de voz, traducción y síntesis con
clonación) enteramente en tu máquina, sin nube. Eso tiene dos consecuencias importantes:

1. **La latencia nunca será cero.** Vas a escuchar cada frase traducida entre
   **1 y 3-4 segundos** después de que la digas (más en equipos de bajos recursos, menos
   con GPU). Es el mismo orden de magnitud que usan los sistemas profesionales de
   interpretación simultánea con IA. "Tiempo real absoluto sin ningún retraso" no es
   posible con un pipeline 100% local en CPU — si en algún momento quieres bajar la
   latencia a casi cero, la única forma es usar servicios en la nube (más rápidos porque
   corren en servidores potentes), que quedan fuera del alcance de este proyecto porque
   pediste que todo sea local y gratis.
2. **La clonación de tu voz solo funciona en ~17 idiomas** (los que soporta XTTS-v2,
   el mejor modelo abierto de clonación de voz que existe hoy: español, inglés,
   portugués, francés, alemán, italiano, neerlandés, polaco, ruso, turco, árabe, chino,
   japonés, coreano, húngaro, checo, hindi). Para el resto de los ~200 idiomas que sí se
   pueden traducir, el sistema sigue funcionando de punta a punta, pero usa una voz
   neutra de buena calidad en vez de tu timbre clonado. Es un límite real de la
   tecnología abierta actual, no algo que se pueda resolver con más código.

## Cómo funciona

```
tu micrófono → VAD (detecta pausas) → Whisper (ASR) → NLLB-200 (traducción)
   → XTTS-v2 clonando tu voz (o Piper si el idioma no tiene clonación)
   → micrófono virtual → tu app de videollamada
```

El "micrófono virtual" es la pieza clave de portabilidad: en vez de integrarse con cada
app de videollamada por separado, clonavoz escribe el audio traducido en un dispositivo
de audio virtual a nivel de sistema operativo. Cualquier app que pueda elegir un
micrófono (Zoom, Meet, Teams, Discord, Skype, lo que sea) puede usarlo como entrada —
por eso funciona con **todo lo que exista**, sin plugins específicos por app.

## Instalación de un solo comando

Requiere Python 3.10+. Los scripts detectan solos si tienes GPU NVIDIA e instalan el
PyTorch correcto (CPU o CUDA) además del resto de dependencias.

**Linux / macOS:**
```bash
git clone <este repositorio>
cd clonador-de-voz
./setup.sh
source .venv/bin/activate
```

**Windows (PowerShell):**
```powershell
git clone <este repositorio>
cd clonador-de-voz
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\setup.ps1
.\.venv\Scripts\Activate.ps1
```

**Docker (Linux / WSL2 con PulseAudio, un solo comando, sin tocar Python del host):**
```bash
docker compose build
docker compose run --rm clonavoz devices
```
En Windows/macOS, Docker Desktop no da acceso confiable al audio en tiempo real del
host — en esos sistemas usa `setup.ps1`/`setup.sh` en lugar de Docker.

La primera vez que uses cada idioma, se descargan sus modelos (Whisper, NLLB-200,
XTTS-v2 y, si aplica, la voz Piper de respaldo) — en total pueden ser varios GB, y
necesitas internet solo para esa descarga inicial. Después de eso, todo funciona sin
conexión.

### Instalación manual (alternativa a los scripts)

```bash
python -m venv .venv
source .venv/bin/activate   # en Windows: .venv\Scripts\activate
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu  # o /whl/cu121 con GPU NVIDIA
pip install -r requirements.txt
pip install -e .
```

## Instalar FFmpeg (necesario en Windows)

Desde PyTorch 2.9 (obligatorio si tenés Python 3.13/3.14, ya que no hay builds de
PyTorch anteriores para esas versiones), `torchaudio` necesita `torchcodec` para
cargar y escribir audio, y `torchcodec` a su vez necesita FFmpeg. En Linux/macOS es
un paquete común (`apt install ffmpeg` / `brew install ffmpeg`, `setup.sh` te avisa
si falta). En Windows es más manual porque `torchcodec` necesita las **DLLs
compartidas** de FFmpeg, no solo el `.exe`:

1. Descargá el build "shared" (con DLLs) de FFmpeg para Windows, por ejemplo desde
   https://github.com/BtbN/FFmpeg-Builds/releases/latest — buscá el archivo
   `ffmpeg-master-latest-win64-gpl-shared.zip`.
2. Extraelo a una carpeta fija, por ejemplo `C:\ffmpeg`.
3. Agregá la subcarpeta `bin` (la que tiene archivos como `avcodec-XX.dll`) a tu
   variable de entorno `PATH`. Desde PowerShell:
   ```powershell
   [Environment]::SetEnvironmentVariable("Path", $env:Path + ";C:\ffmpeg\ffmpeg-master-latest-win64-gpl-shared\bin", "User")
   ```
   (ajustá la ruta exacta al nombre real de la subcarpeta que se extrajo).
4. Cerrá y volvé a abrir PowerShell para que tome el `PATH` nuevo.

Si te salteás este paso vas a ver un error `Could not load libtorchcodec` /
`FFmpeg is not properly installed` al intentar sintetizar voz — ver la sección de
Solución de problemas más abajo.

## Instalar el micrófono virtual (una vez, según tu sistema operativo)

### Windows
Instala [VB-CABLE](https://vb-audio.com/Cable/) (gratis). Tras instalarlo y reiniciar,
aparecerá el dispositivo "CABLE Input" — clonavoz lo detecta solo.

### macOS
Instala [BlackHole](https://existential.audio/blackhole/) (gratis, `brew install blackhole-2ch`).
clonavoz detecta automáticamente cualquier dispositivo con "BlackHole" en el nombre.

### Linux
Ejecuta el script incluido (usa PulseAudio/PipeWire):

```bash
./scripts/linux_create_virtual_mic.sh
```

Esto crea un sink llamado `clonavoz_mic`. Para eliminarlo después:
`./scripts/linux_remove_virtual_mic.sh`.

## Uso

### 1. Verifica tus dispositivos de audio

```bash
clonavoz devices
```

### 2. Graba una muestra de tu voz (una sola vez)

```bash
clonavoz enroll --seconds 15
```

Habla con normalidad, sin ruido de fondo. Se guarda en `~/.clonavoz/mi_voz.wav`.

### 3. Revisa los idiomas disponibles

```bash
clonavoz languages
```

### 4. Inicia la traducción en vivo

```bash
clonavoz run --source-lang es --target-lang en
```

Esto detecta tu hardware (`--profile auto` por defecto: podés forzar `low`, `medium` o
`high` si querés) y detecta el micrófono virtual instalado. Deja esto corriendo y, en
Zoom/Meet/Teams/Discord, selecciona el micrófono virtual (`CABLE Input`, `BlackHole` o
`ClonaVoz_Mic.monitor` según tu sistema) como dispositivo de entrada de audio.

Para el sentido contrario (que ellos te hablen en otro idioma y tú lo escuches en
español), corre una segunda instancia con los idiomas invertidos, escuchando el audio
de salida de la llamada como entrada y reproduciendo hacia tus audífonos.

## Perfiles de rendimiento

| Perfil | Cuándo se usa | Whisper | Latencia aprox. |
|---|---|---|---|
| `low` | Laptop sin GPU, poca RAM | `tiny` | ~3-5 s |
| `medium` | Laptop de gama media / Apple Silicon | `small` | ~1.5-3 s |
| `high` | Notebook gamer con GPU NVIDIA (≥6GB VRAM) | `medium` | ~1-2 s |

`auto` (por defecto) elige el perfil según la RAM, núcleos de CPU y GPU detectados.

## Licencias de los modelos usados

- Whisper (faster-whisper): MIT.
- NLLB-200: CC-BY-NC 4.0 (uso no comercial).
- XTTS-v2: Coqui Public Model License (uso no comercial sin licencia adicional).
- Piper: MIT.

Si planeas un uso comercial, revisa las licencias de NLLB-200 y XTTS-v2 antes.

## Limitaciones conocidas / roadmap

- No hay interfaz gráfica todavía (solo línea de comandos).
- La clonación de voz cross-idioma es de mejor calidad en pares con buen soporte de
  Whisper/XTTS (ej. es↔en, es↔pt); en idiomas del fallback Piper, la calidad de voz es
  buena pero no es tu timbre.
- El fallback Piper (`piper_fallback.py`) es la parte más nueva del código y puede
  necesitar ajustes menores según la versión de `piper-tts` instalada.

## Solución de problemas

**Error `cannot import name 'isin_mps_friendly' from 'transformers.pytorch_utils'`**
al sintetizar voz: significa que se instaló una versión de `transformers` demasiado
nueva para `coqui-tts`. El proyecto ya fija `transformers<5.0.0` en
`requirements.txt`/`pyproject.toml`, pero si instalaste antes de ese cambio, corregilo con:
```bash
pip install "transformers>=4.40,<5.0"
```
y volvé a correr `clonavoz run` (no hace falta reinstalar ni volver a descargar los
modelos ya cacheados).

**Error `torchcodec library is required for audio IO`** al sintetizar voz: instalá
el extra `[codec]` de coqui-tts (ya viene en `requirements.txt`/`pyproject.toml`; si
instalaste antes de ese cambio):
```bash
pip install "coqui-tts[codec]"
```

**Error `Could not load libtorchcodec` / `FFmpeg is not properly installed`** al
sintetizar voz (en Windows, típicamente después de instalar `torchcodec`): te falta
FFmpeg como DLLs compartidas en el `PATH`. Seguí la sección **"Instalar FFmpeg
(necesario en Windows)"** más arriba — no es un problema de versión de PyTorch, así
que no sirve bajar de versión (además, en Python 3.13/3.14 no existen builds de
PyTorch anteriores a la 2.9, que es justo la que introduce esta dependencia).
