# Micrófono virtual VB-CABLE (de VB-Audio Software, www.vb-cable.com: es
# donationware, se aceptan donaciones). Viene tal cual, sin modificar, en la
# carpeta "vbcable": este script abre su instalador oficial, que pide permiso
# de administrador (se instala una vez por PC) y tiene los botones "Install
# Driver" y "Remove Driver". Lo usa el menú (Iniciar.bat).
#
#   vbcable.ps1                 abre el instalador oficial (instalar o quitar)
#   vbcable.ps1 -Verificar      sale con 0 si ya está instalado en esta PC
#   vbcable.ps1 -Predeterminados  muestra la salida y la entrada predeterminadas
#
# Al instalarlo, Windows a veces deja a VB-CABLE como parlante y micrófono
# predeterminados (y dejás de escuchar la PC, o la llamada escucharía su propio
# audio): después del instalador se vuelven a poner los que tenías.
param([switch]$Verificar, [switch]$Predeterminados)
$ErrorActionPreference = "Stop"

function Test-VBCable {
    $devices = Get-CimInstance -ClassName Win32_SoundDevice -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like "*VB-Audio Virtual Cable*" -and $_.StatusInfo -ne 4 }
    return [bool]$devices
}

Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
namespace ClonavozAudio {
    [Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IMMDevice {
        [PreserveSig] int Activate(ref Guid iid, int clsCtx, IntPtr activationParams, out IntPtr iface);
        [PreserveSig] int OpenPropertyStore(int access, out IntPtr store);
        [PreserveSig] int GetId([MarshalAs(UnmanagedType.LPWStr)] out string id);
        [PreserveSig] int GetState(out int state);
    }
    [Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IMMDeviceEnumerator {
        [PreserveSig] int EnumAudioEndpoints(int dataFlow, int stateMask, out IntPtr devices);
        [PreserveSig] int GetDefaultAudioEndpoint(int dataFlow, int role, out IMMDevice endpoint);
        [PreserveSig] int GetDevice([MarshalAs(UnmanagedType.LPWStr)] string id, out IMMDevice device);
    }
    [ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")] class MMDeviceEnumerator { }
    // Interfaz sin documentar (la usan desde Windows 7 las herramientas que
    // cambian el dispositivo predeterminado): solo importa SetDefaultEndpoint.
    [Guid("F8679F50-850A-41CF-9C72-430F290290C8"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IPolicyConfig {
        [PreserveSig] int GetMixFormat();
        [PreserveSig] int GetDeviceFormat();
        [PreserveSig] int ResetDeviceFormat();
        [PreserveSig] int SetDeviceFormat();
        [PreserveSig] int GetProcessingPeriod();
        [PreserveSig] int SetProcessingPeriod();
        [PreserveSig] int GetShareMode();
        [PreserveSig] int SetShareMode();
        [PreserveSig] int GetPropertyValue();
        [PreserveSig] int SetPropertyValue();
        [PreserveSig] int SetDefaultEndpoint([MarshalAs(UnmanagedType.LPWStr)] string id, int role);
        [PreserveSig] int SetEndpointVisibility();
    }
    [ComImport, Guid("870AF99C-171D-4F9E-AF0D-E63DF40C2BC9")] class PolicyConfigClient { }

    public static class Defaults {
        // flow: 0 = salida (parlantes/auriculares), 1 = entrada (micrófono)
        public static string Get(int flow) {
            var enumerator = (IMMDeviceEnumerator)new MMDeviceEnumerator();
            IMMDevice device;
            if (enumerator.GetDefaultAudioEndpoint(flow, 0, out device) != 0 || device == null) return null;
            string id;
            return device.GetId(out id) == 0 ? id : null;
        }
        public static bool IsActive(string id) {
            var enumerator = (IMMDeviceEnumerator)new MMDeviceEnumerator();
            IMMDevice device;
            int state;
            if (enumerator.GetDevice(id, out device) != 0 || device == null) return false;
            return device.GetState(out state) == 0 && state == 1;  // DEVICE_STATE_ACTIVE
        }
        public static int Set(string id) {
            var policy = (IPolicyConfig)new PolicyConfigClient();
            int result = 0;
            for (int role = 0; role < 3; role++) {  // consola, multimedia y comunicaciones
                int hr = policy.SetDefaultEndpoint(id, role);
                if (hr != 0) result = hr;
            }
            return result;
        }
    }
}
"@

function Get-DefaultDevices {
    return @{ Salida = [ClonavozAudio.Defaults]::Get(0); Entrada = [ClonavozAudio.Defaults]::Get(1) }
}

function Restore-DefaultDevices($before) {
    $names = @{ Salida = "parlante/auricular"; Entrada = "micrófono" }
    foreach ($kind in "Salida", "Entrada") {
        $old = $before[$kind]
        $flow = if ($kind -eq "Salida") { 0 } else { 1 }
        $now = [ClonavozAudio.Defaults]::Get($flow)
        if ($old -and $now -ne $old -and [ClonavozAudio.Defaults]::IsActive($old)) {
            if ([ClonavozAudio.Defaults]::Set($old) -eq 0) {
                Write-Host "Listo: se volvió a poner tu $($names[$kind]) de antes como predeterminado."
            }
        }
    }
}

if ($Verificar) {
    if (Test-VBCable) { exit 0 } else { exit 1 }
}

if ($Predeterminados) {
    $defaults = Get-DefaultDevices
    Write-Host "Salida predeterminada: $($defaults.Salida)"
    Write-Host "Entrada predeterminada: $($defaults.Entrada)"
    foreach ($id in $defaults.Values) {
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

$before = Get-DefaultDevices
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
