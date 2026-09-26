# Micrófono virtual VB-CABLE (de VB-Audio Software, www.vb-cable.com: es
# donationware, se aceptan donaciones). Viene tal cual, sin modificar, en la
# carpeta "vbcable": este script abre su instalador oficial, que pide permiso
# de administrador (se instala una vez por PC) y tiene los botones "Install
# Driver" y "Remove Driver". Lo usan el menú (Iniciar.bat) y clonavoz.exe.
#
#   vbcable.ps1                   abre el instalador oficial (instalar o quitar)
#   vbcable.ps1 -Verificar        sale con 0 si ya está instalado en esta PC
#   vbcable.ps1 -UsarCable        pone "CABLE Output" como micrófono predeterminado
#                                 de Windows (así la videollamada lo usa sin elegir
#                                 nada) y muestra el nombre de tu micrófono real
#   vbcable.ps1 -Restaurar        vuelve a poner el micrófono predeterminado de antes
#   vbcable.ps1 -Predeterminados  muestra la salida y la entrada predeterminadas
#
# Al instalarlo, Windows a veces deja a VB-CABLE como parlante y micrófono
# predeterminados (y dejás de escuchar la PC, o la llamada escucharía su propio
# audio): después del instalador se vuelven a poner los que tenías.
param([switch]$Verificar, [switch]$Predeterminados, [switch]$UsarCable, [switch]$Restaurar)
$ErrorActionPreference = "Stop"

function Test-VBCable {
    $devices = Get-CimInstance -ClassName Win32_SoundDevice -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like "*VB-Audio Virtual Cable*" -and $_.StatusInfo -ne 4 }
    return [bool]$devices
}

if ($Verificar) {
    if (Test-VBCable) { exit 0 } else { exit 1 }
}

# Los dispositivos predeterminados de Windows (el mismo código que usa la ventana
# de clonavoz, ver AudioDefaults.cs).
Add-Type -Path (Join-Path $PSScriptRoot "AudioDefaults.cs")

function Restore-DefaultDevices($before) {
    $restored = [ClonavozAudio.Defaults]::RestoreSnapshot($before)
    if ($restored -band 1) { Write-Host "Listo: se volvió a poner tu parlante/auricular de antes como predeterminado." }
    if ($restored -band 2) { Write-Host "Listo: se volvió a poner tu micrófono de antes como predeterminado." }
}

# Dónde se guarda cuál era tu micrófono predeterminado (en la carpeta "datos",
# o sea en el pendrive), para volver a ponerlo aunque clonavoz se cierre mal.
$saved = Join-Path $PSScriptRoot "datos\microfono_anterior.txt"

if ($UsarCable) {
    $real = [ClonavozAudio.Defaults]::UseCable($saved)
    if ($null -eq $real) { Write-Host "No se pudo poner CABLE Output como micrófono predeterminado (¿está instalado VB-CABLE?)."; exit 1 }
    if ($real) { Write-Output "MICROFONO_REAL=$real" }
    exit 0
}

if ($Restaurar) {
    if ([ClonavozAudio.Defaults]::Restore($saved)) { exit 0 } else { exit 1 }
}

if ($Predeterminados) {
    $defaults = [ClonavozAudio.Defaults]::Snapshot()
    Write-Host "Salida predeterminada: $($defaults[0]) ($([ClonavozAudio.Defaults]::Name($defaults[0])))"
    Write-Host "Entrada predeterminada: $($defaults[1]) ($([ClonavozAudio.Defaults]::Name($defaults[1])))"
    foreach ($id in $defaults) {
        if ($id -and [ClonavozAudio.Defaults]::Set($id) -ne 0) { exit 1 }  # volver a poner el mismo: no cambia nada
    }
    exit 0
}

$zip = Join-Path $PSScriptRoot "vbcable\VBCABLE_Driver_Pack45.zip"
if (-not (Test-Path $zip)) {
    Write-Host "Esta copia de clonavoz no trae el instalador de VB-CABLE: se abre la página oficial."
    Write-Host "Descargá el ZIP, extraelo y ejecutá VBCABLE_Setup_x64.exe como administrador."
    Start-Process "https://vb-audio.com/Cable/"
    exit 2
}

$before = [ClonavozAudio.Defaults]::Snapshot()
$folder = Join-Path $env:TEMP "clonavoz-vbcable"
if (Test-Path $folder) { Remove-Item -Recurse -Force $folder }
Expand-Archive -Path $zip -DestinationPath $folder -Force
$setup = Join-Path $folder "VBCABLE_Setup_x64.exe"
Write-Host ""
Write-Host "Se abre el instalador oficial de VB-CABLE (de VB-Audio). Windows va a pedir permiso"
Write-Host "de administrador: Sí. En el instalador: 'Install Driver' para instalarlo (o 'Remove"
Write-Host "Driver' para quitarlo) y, si Windows pregunta si instalar el software de dispositivo"
Write-Host "de VB-Audio, 'Instalar'."
try {
    Start-Process -FilePath $setup -WorkingDirectory $folder -Verb RunAs -Wait
} catch {
    Write-Host "No se abrió el instalador: hace falta aceptar el permiso de administrador."
    exit 1
}
Start-Sleep -Seconds 3
Restore-DefaultDevices $before
Remove-Item -Recurse -Force $folder -ErrorAction SilentlyContinue
if (Test-VBCable) {
    Write-Host "VB-CABLE está instalado. En la videollamada elegí como micrófono: CABLE Output."
    Write-Host "(Si no aparece en la videollamada, reiniciá la PC una vez.)"
    exit 0
}
Write-Host "VB-CABLE no está instalado en esta PC (si recién lo instalaste, reiniciá la PC)."
exit 1
