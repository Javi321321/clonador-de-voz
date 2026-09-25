# Solo para la prueba automática con audio real en GitHub Actions (ver
# .github/workflows/portable-windows.yml): esas máquinas Windows no tienen
# placa de sonido y tienen el audio apagado. Este script lo enciende e instala
# el cable virtual VB-CABLE oficial. (Una segunda copia no sirve: Windows la
# crea pero no arranca, CM_PROB_FAILED_START.)
#
# En tu computadora VB-CABLE se instala con su instalador normal (ver
# LEEME.txt): clonavoz no lo incluye ni lo instala.
$ErrorActionPreference = "Stop"

Write-Host "== Servicio de audio de Windows =="
foreach ($name in "AudioEndpointBuilder", "Audiosrv") {
    Set-Service -Name $name -StartupType Automatic
    Start-Service -Name $name
}
Get-Service AudioEndpointBuilder, Audiosrv | Format-Table -AutoSize Name, Status, StartType

Write-Host "== Permiso de micrófono para los programas de escritorio =="
# Lo mismo que Configuración > Privacidad > Micrófono en una PC normal.
function Set-RegistryValue($Path, $Name, $Value, $Type = "String") {
    if (-not (Test-Path $Path)) { New-Item -Path $Path -Force | Out-Null }
    Set-ItemProperty -Path $Path -Name $Name -Value $Value -Type $Type
}
foreach ($hive in "HKLM:", "HKCU:") {
    $key = "$hive\SOFTWARE\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"
    Set-RegistryValue $key Value Allow
    Set-RegistryValue "$key\NonPackaged" Value Allow
}
Set-RegistryValue "HKLM:\SOFTWARE\Policies\Microsoft\Windows\AppPrivacy" LetAppsAccessMicrophone 1 DWord

Write-Host "== VB-CABLE (paquete oficial de vb-audio.com) =="
$url = "https://download.vb-audio.com/Download_CABLE/VBCABLE_Driver_Pack45.zip"
$sha256 = "B950E39F01AF1D04EA623C8F6D8EB9B6EA5C477C637295FABF20631C85116BFB"
$dir = Join-Path $env:RUNNER_TEMP "vbcable"
$zip = "$dir.zip"
Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
$hash = (Get-FileHash -Algorithm SHA256 $zip).Hash
if ($hash -ne $sha256) {
    throw "El paquete descargado no es el esperado (SHA256 $hash). Si VB-Audio publicó uno nuevo, revisarlo y actualizar el hash."
}
Expand-Archive -Path $zip -DestinationPath $dir -Force
$inf = Join-Path $dir "vbMmeCable64_win10.inf"
$signature = Get-AuthenticodeSignature (Join-Path $dir "vbaudio_cable64_win10.cat")
Write-Host "Firma del driver: $($signature.Status) - $($signature.SignerCertificate.Subject)"
if ($signature.Status -ne "Valid") { throw "La firma del driver no es válida" }
# Confiar en quien firmó el driver, para que Windows lo instale sin preguntar.
$store = New-Object System.Security.Cryptography.X509Certificates.X509Store("TrustedPublisher", "LocalMachine")
$store.Open("ReadWrite")
$store.Add($signature.SignerCertificate)
$store.Close()

# Crear el dispositivo (como `devcon install`): con el devcon del Windows
# Driver Kit si la máquina lo tiene, y si no, con las mismas funciones de
# Windows (SetupAPI) llamadas directamente.
$devcon = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\Tools" -Recurse -Filter devcon.exe -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -match '\\x64\\' } | Select-Object -First 1
if (-not $devcon) {
    Add-Type -TypeDefinition @"
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Text;

public static class RootDevice {
    [StructLayout(LayoutKind.Sequential)]
    struct SP_DEVINFO_DATA { public int cbSize; public Guid ClassGuid; public int DevInst; public IntPtr Reserved; }

    [DllImport("setupapi.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool SetupDiGetINFClassW(string inf, out Guid classGuid, StringBuilder className, int size, IntPtr required);
    [DllImport("setupapi.dll", SetLastError = true)]
    static extern IntPtr SetupDiCreateDeviceInfoList(ref Guid classGuid, IntPtr parent);
    [DllImport("setupapi.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool SetupDiCreateDeviceInfoW(IntPtr set, string name, ref Guid classGuid, string description,
        IntPtr parent, int flags, ref SP_DEVINFO_DATA data);
    [DllImport("setupapi.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool SetupDiSetDeviceRegistryPropertyW(IntPtr set, ref SP_DEVINFO_DATA data, int property,
        byte[] buffer, int size);
    [DllImport("setupapi.dll", SetLastError = true)]
    static extern bool SetupDiCallClassInstaller(int function, IntPtr set, ref SP_DEVINFO_DATA data);
    [DllImport("setupapi.dll", SetLastError = true)]
    static extern bool SetupDiDestroyDeviceInfoList(IntPtr set);
    [DllImport("newdev.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool UpdateDriverForPlugAndPlayDevicesW(IntPtr parent, string hardwareId, string inf, int flags,
        out bool reboot);

    public static void Install(string inf, string hardwareId) {
        Guid classGuid;
        var className = new StringBuilder(256);
        if (!SetupDiGetINFClassW(inf, out classGuid, className, className.Capacity, IntPtr.Zero))
            throw new Win32Exception();
        IntPtr set = SetupDiCreateDeviceInfoList(ref classGuid, IntPtr.Zero);
        if (set == new IntPtr(-1)) throw new Win32Exception();
        try {
            var data = new SP_DEVINFO_DATA();
            data.cbSize = Marshal.SizeOf(typeof(SP_DEVINFO_DATA));
            const int DICD_GENERATE_ID = 1, SPDRP_HARDWAREID = 1, DIF_REGISTERDEVICE = 0x19;
            if (!SetupDiCreateDeviceInfoW(set, className.ToString(), ref classGuid, null, IntPtr.Zero,
                    DICD_GENERATE_ID, ref data))
                throw new Win32Exception();
            byte[] ids = Encoding.Unicode.GetBytes(hardwareId + "\0\0");
            if (!SetupDiSetDeviceRegistryPropertyW(set, ref data, SPDRP_HARDWAREID, ids, ids.Length))
                throw new Win32Exception();
            if (!SetupDiCallClassInstaller(DIF_REGISTERDEVICE, set, ref data)) throw new Win32Exception();
        } finally {
            SetupDiDestroyDeviceInfoList(set);
        }
        bool reboot;
        const int INSTALLFLAG_FORCE = 1;
        if (!UpdateDriverForPlugAndPlayDevicesW(IntPtr.Zero, hardwareId, inf, INSTALLFLAG_FORCE, out reboot))
            throw new Win32Exception();
    }
}
"@
}
if ($devcon) {
    Write-Host "Instalando con $($devcon.FullName)"
    & $devcon.FullName install $inf VBAudioVACWDM
    if ($LASTEXITCODE -gt 1) { throw "devcon install falló (código $LASTEXITCODE)" }
} else {
    Write-Host "Instalando con SetupAPI (no se encontró devcon)"
    [RootDevice]::Install($inf, "VBAudioVACWDM")
}

Write-Host "== Dispositivos de audio que ve Windows =="
# Son 3: la entrada ("CABLE Input", aunque acá Windows la llama "Speakers"),
# "CABLE In 16 Ch" y "CABLE Output".
$deadline = (Get-Date).AddSeconds(60)
do {
    Start-Sleep -Seconds 2
    $endpoints = @(Get-PnpDevice -Class AudioEndpoint -PresentOnly -ErrorAction SilentlyContinue)
} until ($endpoints.Count -ge 3 -or (Get-Date) -gt $deadline)
Get-PnpDevice -Class MEDIA -PresentOnly -ErrorAction SilentlyContinue |
    Format-Table -AutoSize Status, Problem, FriendlyName, InstanceId
$endpoints | Format-Table -AutoSize Status, FriendlyName
if ($endpoints.Count -eq 0) { throw "Windows no creó ningún dispositivo de audio" }
exit 0  # si no, GitHub toma el código de salida de devcon (1 = "reiniciar")
