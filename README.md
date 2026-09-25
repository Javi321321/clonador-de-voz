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

1. **La traducción no puede salir en el mismo instante en que hablás.** Ningún
   traductor (ni una persona intérprete) puede: para traducir una frase hay que
   escucharla primero, y los idiomas ordenan las palabras distinto (el verbo, por
   ejemplo, puede ir al final). Los intérpretes profesionales van 2-3 segundos atrás.
   clonavoz traduce cada frase apenas hacés una pausa: en nuestras pruebas, sin GPU,
   la traducción terminó de sonar **unos 4 segundos** después de que terminaste de
   hablar, tanto con 4 núcleos como con 2 (una computadora modesta). En frases largas,
   la primera parte ya empieza a sonar mientras seguís hablando.
2. **Tu voz clonada funciona en ~37 idiomas**, todos los que tienen una voz de Piper
   (español, inglés, portugués, francés, alemán, italiano, neerlandés, polaco, ruso,
   turco, árabe, chino, japonés, coreano, húngaro, checo, hindi, ucraniano, sueco,
   noruego, danés, finés, griego, rumano, búlgaro, eslovaco, serbio, catalán, euskera,
   hebreo, vietnamita, tailandés, indonesio, bengalí, persa, urdu y suajili). Para el
   resto de los ~200 idiomas que se pueden traducir, hay que agregar una voz a mano
   (ver `piper_tts.py`). El parecido con tu voz es muy bueno pero no perfecto, y la
   entonación de cada frase la pone el modelo: no copia exactamente cómo la dijiste.

## Cómo funciona

```
tu micrófono → VAD (detecta pausas) → Whisper (ASR) → NLLB-200 (traducción)
   → Piper (voz rápida) + OpenVoice (le pone tu timbre)   ← sin GPU (por defecto)
     o XTTS-v2 clonando tu voz                            ← con GPU NVIDIA
   → micrófono virtual → tu app de videollamada
```

Hay dos motores para generar tu voz (se elige solo, o con `--voice-engine`):

| Motor | Cuándo se usa | Voz de una frase de ~3 s | Memoria | Idiomas con tu voz |
|---|---|---|---|---|
| `openvoice` (Piper + OpenVoice) | sin GPU NVIDIA | ~0.6 s (4 núcleos), ~0.9 s (2 núcleos) | ~2 GB en total | ~37 |
| `xtts` (XTTS-v2) | con GPU NVIDIA | ~4.5 s (4 núcleos), ~9 s (2 núcleos) sin GPU | ~6 GB en total | 17 |

En nuestras pruebas con la voz de una persona real, los dos se parecen igual a la voz
original (0.92-0.93 en una escala donde la misma persona en otra grabación da 0.98 y
otra persona ~0.66). XTTS-v2 tiene una entonación algo más natural; con GPU es rápido,
sin GPU es varias veces más lento.

El "micrófono virtual" es la pieza clave de portabilidad: en vez de integrarse con cada
app de videollamada por separado, clonavoz escribe el audio traducido en un dispositivo
de audio virtual a nivel de sistema operativo. Cualquier app que pueda elegir un
micrófono (Zoom, Meet, Teams, Discord, Skype, lo que sea) puede usarlo como entrada —
por eso funciona con **todo lo que exista**, sin plugins específicos por app.

## Instalación de un solo comando

Requiere Python 3.10+. Los scripts detectan solos si tienes GPU NVIDIA: sin GPU instalan
el motor de voz liviano (no hace falta FFmpeg); con GPU instalan además PyTorch con CUDA
y el motor XTTS-v2.

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

La primera vez se descargan los modelos (Whisper, NLLB-200, el conversor de OpenVoice y
la voz de Piper de cada idioma que uses; con XTTS-v2, también ese modelo): alrededor de
1 GB con el motor liviano, varios GB con XTTS-v2. Necesitas internet solo para esa
descarga inicial; después, todo funciona sin conexión.

### Instalación manual (alternativa a los scripts)

```bash
python -m venv .venv
source .venv/bin/activate   # en Windows: .venv\Scripts\activate
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt
pip install -e .
```

Para el motor XTTS-v2 (recomendado solo con GPU NVIDIA), además:
```bash
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121
pip install -r requirements-xtts.txt
```

## Instalar FFmpeg (solo para el motor XTTS-v2 en Windows)

El motor liviano (el que se usa sin GPU) no necesita FFmpeg. Con XTTS-v2 sí: desde PyTorch 2.9 (obligatorio si tenés Python 3.13/3.14, ya que no hay builds de
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
aparecen dos dispositivos, que son las dos puntas del mismo cable:

- **"CABLE Input"** (dispositivo de *salida*): ahí clonavoz reproduce tu voz traducida.
  clonavoz lo detecta solo.
- **"CABLE Output"** (dispositivo de *entrada*/micrófono): es el que tenés que elegir como
  micrófono en Zoom/Meet/Teams/Discord.

Ojo: al instalar VB-CABLE, Windows a veces deja "CABLE Output" como micrófono
predeterminado y "CABLE Input" como altavoz predeterminado. Volvé a poner tu micrófono y
tus parlantes/auriculares reales como predeterminados en *Configuración > Sistema >
Sonido*. (clonavoz igual detecta ese caso y usa tu micrófono real, avisándote en pantalla.)

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

Marca cuál es tu micrófono predeterminado, cuáles son micrófonos virtuales, y te dice qué
micrófono elegir en la app de videollamada.

### 2. Probá tu micrófono y el micrófono virtual

```bash
clonavoz test-audio
```

Primero muestra un medidor de nivel de tu micrófono durante 8 segundos mientras hablás
(tiene que moverse y decir `VOZ DETECTADA`); después reproduce 3 pitidos en el micrófono
virtual y comprueba que lleguen a la otra punta del cable (por ejemplo, a "CABLE Output").
Si algo falla, te dice la causa probable. Podés elegir otros dispositivos con
`--input-device N` / `--output-device N` (los números salen de `clonavoz devices`).

### 3. Graba una muestra de tu voz (una sola vez)

```bash
clonavoz enroll --seconds 15
```

Habla con normalidad, sin ruido de fondo. Se guarda en `~/.clonavoz/mi_voz.wav`. Si la
grabación queda en silencio (micrófono equivocado o bloqueado), no se guarda y te avisa.

### 4. Revisa los idiomas disponibles

```bash
clonavoz languages
```

### 5. Inicia la traducción en vivo

```bash
clonavoz run --source-lang es --target-lang en
```

Esto detecta tu hardware (`--profile auto` por defecto: podés forzar `low`, `medium` o
`high` si querés) y detecta el micrófono virtual instalado. Deja esto corriendo y, en
Zoom/Meet/Teams/Discord, selecciona como micrófono la otra punta del cable virtual:
**`CABLE Output`** en Windows, **`BlackHole 2ch`** en macOS o **`Monitor of ClonaVoz_Mic`**
en Linux (clonavoz te lo recuerda al arrancar).

Mientras corre, una línea de estado muestra el nivel de tu micrófono y en qué está:

```
Mic [###########-----]  -14 dB | hablando
Mic [----------------]  -60 dB | traduciendo 1
Mic [----------------]  -60 dB | reproduciendo
```

El micrófono virtual **no se mueve al mismo tiempo que tu voz**: se mueve cuando cada
frase traducida está lista (`reproduciendo`), unos segundos después de decirla.

Para el sentido contrario (que ellos te hablen en otro idioma y tú lo escuches en
español), corre una segunda instancia con los idiomas invertidos, escuchando el audio
de salida de la llamada como entrada y reproduciendo hacia tus audífonos.

## Perfiles de rendimiento

| Perfil | Cuándo se usa | Whisper | Motor de voz |
|---|---|---|---|
| `low` | Laptop sin GPU, poca RAM | `tiny` | `openvoice` (liviano) |
| `medium` | Laptop de gama media / Apple Silicon | `small` | `openvoice` (liviano) |
| `high` | Notebook gamer con GPU NVIDIA (≥6GB VRAM) | `medium` | `xtts` (XTTS-v2) |

`auto` (por defecto) elige el perfil según la RAM, núcleos de CPU y GPU detectados. Con
el perfil `low`, en nuestras pruebas sin GPU (con 4 y con 2 núcleos) la traducción de una
frase de 6 segundos terminó de sonar ~4 segundos después de terminar de hablar, usando
~2 GB de memoria. `medium` reconoce mejor lo que decís (Whisper `small`) a cambio de un
poco más de demora.

## Licencias de los modelos usados

- Whisper (faster-whisper): MIT.
- NLLB-200: CC-BY-NC 4.0 (uso no comercial). Se usa una conversión a CTranslate2 del
  mismo modelo, con la misma licencia.
- OpenVoice V2 (conversor de timbre, incluido en `openvoice.py`): MIT.
- Piper: MIT. Cada voz tiene su propia licencia: la mayoría de las elegidas son CC0,
  dominio público o CC-BY (permiten uso comercial). Las de turco, japonés, coreano,
  hindi, serbio y tailandés son de uso no comercial, y las de árabe, chino, hebreo,
  indonesio, suajili y las voces agudas de ruso y sueco no declaran una licencia clara
  (ver el `MODEL_CARD` de cada voz en https://huggingface.co/rhasspy/piper-voices).
- XTTS-v2 (motor opcional): Coqui Public Model License (uso no comercial sin licencia
  adicional).

Si planeas un uso comercial: con el motor liviano, lo único de uso no comercial es
NLLB-200 (y las voces de Piper mencionadas); con XTTS-v2, también ese modelo.

## Limitaciones conocidas / roadmap

- No hay interfaz gráfica todavía (solo línea de comandos).
- La voz clonada copia tu timbre, pero la entonación de cada frase la pone el modelo
  (Piper o XTTS-v2): no copia la emoción exacta con la que la dijiste.
- El motor liviano elige, para cada idioma, una voz base grave o aguda según el tono de
  tu muestra de voz. En algunos idiomas hay una sola voz disponible (por ejemplo, alemán
  e italiano), y ahí el parecido puede ser algo menor si tu tono es muy distinto.
- Japonés y tailandés necesitan un paquete extra de Python para leer ese idioma
  (`pip install pyopenjtalk` / `pip install tltk`; el error te lo indica). Croata,
  gallego y malayo todavía no tienen voz de Piper: se puede agregar una en
  `~/.clonavoz/piper_voices.json`.

## Solución de problemas

**Hablo y el micrófono no se mueve.** Primero corré `clonavoz test-audio`: te dice si el
problema está en tu micrófono o en el micrófono virtual, y la causa probable. Las más
comunes:

- **Estás mirando el micrófono virtual mientras hablás.** "CABLE Output" (o "BlackHole
  2ch" / "Monitor of ClonaVoz_Mic") recién se mueve cuando la frase traducida está lista,
  unos segundos *después* de decirla. En la línea de estado de
  `clonavoz run`, `Mic [####...]` tiene que moverse mientras hablás y después pasar a
  `traduciendo` y `reproduciendo`: ahí es cuando se mueve el micrófono virtual.
- **clonavoz está escuchando otro micrófono.** Si `Mic [...]` no se mueve cuando hablás,
  elegí tu micrófono con `--input-device N` (los números salen de `clonavoz devices`). Si
  el micrófono predeterminado de Windows quedó en "CABLE Output" (pasa al instalar
  VB-CABLE), clonavoz lo detecta y usa tu micrófono real, pero conviene volver a poner el
  tuyo como predeterminado en *Configuración > Sistema > Sonido*.
- **Windows bloquea el micrófono** (el medidor queda en `-60 dB` y aparece el aviso de
  "silencio absoluto"): *Configuración > Privacidad y seguridad > Micrófono* y activá
  "Permitir que las aplicaciones de escritorio accedan al micrófono".
- **En la videollamada elegiste el dispositivo equivocado:** el micrófono a elegir es
  "CABLE Output", no "CABLE Input" (ni tu micrófono real).
- **Aparece `Error procesando una frase`:** la frase se escuchó pero no se pudo generar el
  audio, así que no sale nada. Mirá los errores de abajo (FFmpeg, `transformers`). Si
  usás XTTS-v2 sin GPU, probá el motor liviano: `--voice-engine openvoice`.

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
