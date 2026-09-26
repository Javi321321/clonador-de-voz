// Los dispositivos de audio predeterminados de Windows: cuáles son, y
// cambiarlos (por ejemplo, poner "CABLE Output" de VB-CABLE como micrófono
// predeterminado mientras clonavoz traduce, para que la videollamada lo use
// sin tener que elegir nada, y después volver a poner el tuyo).
//
// Lo usan la ventana de clonavoz (clonavoz.exe lo compila adentro) y
// vbcable.ps1 (Add-Type -Path). Es C# 5, lo que compila el Windows de fábrica.
using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;

namespace ClonavozAudio
{
    [StructLayout(LayoutKind.Sequential)]
    struct PropertyKey
    {
        public Guid fmtid;
        public int pid;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct PropVariant
    {
        public ushort vt;
        public ushort r1;
        public ushort r2;
        public ushort r3;
        public IntPtr p1;
        public IntPtr p2;
    }

    [Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IPropertyStore
    {
        [PreserveSig] int GetCount(out int count);
        [PreserveSig] int GetAt(int index, out PropertyKey key);
        [PreserveSig] int GetValue(ref PropertyKey key, out PropVariant value);
    }

    [Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IMMDevice
    {
        [PreserveSig] int Activate(ref Guid iid, int clsCtx, IntPtr activationParams, out IntPtr iface);
        [PreserveSig] int OpenPropertyStore(int access, out IPropertyStore store);
        [PreserveSig] int GetId([MarshalAs(UnmanagedType.LPWStr)] out string id);
        [PreserveSig] int GetState(out int state);
    }

    [Guid("0BD7A1BE-7A1A-44DB-8397-CC5392387B5E"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IMMDeviceCollection
    {
        [PreserveSig] int GetCount(out int count);
        [PreserveSig] int Item(int index, out IMMDevice device);
    }

    [Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IMMDeviceEnumerator
    {
        [PreserveSig] int EnumAudioEndpoints(int dataFlow, int stateMask, out IMMDeviceCollection devices);
        [PreserveSig] int GetDefaultAudioEndpoint(int dataFlow, int role, out IMMDevice endpoint);
        [PreserveSig] int GetDevice([MarshalAs(UnmanagedType.LPWStr)] string id, out IMMDevice device);
    }

    [ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")]
    class MMDeviceEnumerator
    {
    }

    // El volumen y el silencio de un dispositivo (el que ves en Configuración > Sonido).
    [Guid("5CDF2C82-841E-4546-9722-0CF74078229A"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IAudioEndpointVolume
    {
        [PreserveSig] int RegisterControlChangeNotify(IntPtr notify);
        [PreserveSig] int UnregisterControlChangeNotify(IntPtr notify);
        [PreserveSig] int GetChannelCount(out uint count);
        [PreserveSig] int SetMasterVolumeLevel(float levelDB, ref Guid context);
        [PreserveSig] int SetMasterVolumeLevelScalar(float level, ref Guid context);
        [PreserveSig] int GetMasterVolumeLevel(out float levelDB);
        [PreserveSig] int GetMasterVolumeLevelScalar(out float level);
        [PreserveSig] int SetChannelVolumeLevel(uint channel, float levelDB, ref Guid context);
        [PreserveSig] int SetChannelVolumeLevelScalar(uint channel, float level, ref Guid context);
        [PreserveSig] int GetChannelVolumeLevel(uint channel, out float levelDB);
        [PreserveSig] int GetChannelVolumeLevelScalar(uint channel, out float level);
        [PreserveSig] int SetMute([MarshalAs(UnmanagedType.Bool)] bool mute, ref Guid context);
        [PreserveSig] int GetMute([MarshalAs(UnmanagedType.Bool)] out bool mute);
    }

    // Interfaz sin documentar (la usan desde Windows 7 las herramientas que
    // cambian el dispositivo predeterminado): solo importa SetDefaultEndpoint.
    [Guid("F8679F50-850A-41CF-9C72-430F290290C8"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IPolicyConfig
    {
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

    [ComImport, Guid("870AF99C-171D-4F9E-AF0D-E63DF40C2BC9")]
    class PolicyConfigClient
    {
    }

    // flow: 0 = salida (parlantes, auriculares), 1 = entrada (micrófonos).
    // role: 0 = consola, 1 = multimedia, 2 = comunicaciones (el que usan
    // muchas apps de llamadas).
    public static class Defaults
    {
        [DllImport("ole32.dll")]
        static extern int PropVariantClear(ref PropVariant value);

        static IMMDeviceEnumerator Enumerator()
        {
            return (IMMDeviceEnumerator)new MMDeviceEnumerator();
        }

        public static string Get(int flow, int role)
        {
            try
            {
                IMMDevice device;
                if (Enumerator().GetDefaultAudioEndpoint(flow, role, out device) != 0 || device == null) return null;
                string id;
                return device.GetId(out id) == 0 ? id : null;
            }
            catch (Exception)
            {
                return null;  // sin servicio de audio o sin dispositivos
            }
        }

        public static string Get(int flow)
        {
            return Get(flow, 0);
        }

        public static bool IsActive(string id)
        {
            try
            {
                IMMDevice device;
                int state;
                if (id == null || Enumerator().GetDevice(id, out device) != 0 || device == null) return false;
                return device.GetState(out state) == 0 && state == 1;  // DEVICE_STATE_ACTIVE
            }
            catch (Exception)
            {
                return false;
            }
        }

        public static string Name(string id)
        {
            try
            {
                IMMDevice device;
                if (id == null || Enumerator().GetDevice(id, out device) != 0 || device == null) return null;
                return Name(device);
            }
            catch (Exception)
            {
                return null;
            }
        }

        static string Name(IMMDevice device)
        {
            IPropertyStore store;
            if (device.OpenPropertyStore(0, out store) != 0 || store == null) return null;
            var key = new PropertyKey();
            key.fmtid = new Guid("A45C254E-DF1C-4EFD-8020-67D146A850E0");  // PKEY_Device_FriendlyName
            key.pid = 14;
            PropVariant value;
            if (store.GetValue(ref key, out value) != 0) return null;
            string name = value.vt == 31 ? Marshal.PtrToStringUni(value.p1) : null;  // VT_LPWSTR
            PropVariantClear(ref value);
            return name;
        }

        // Los dispositivos conectados de un tipo: pares {id, nombre}.
        public static List<string[]> List(int flow)
        {
            var found = new List<string[]>();
            try
            {
                IMMDeviceCollection devices;
                if (Enumerator().EnumAudioEndpoints(flow, 1, out devices) != 0 || devices == null) return found;
                int count;
                devices.GetCount(out count);
                for (int i = 0; i < count; i++)
                {
                    IMMDevice device;
                    string id;
                    if (devices.Item(i, out device) != 0 || device == null || device.GetId(out id) != 0) continue;
                    found.Add(new string[] { id, Name(device) ?? "" });
                }
            }
            catch (Exception)
            {
            }
            return found;
        }

        // El dispositivo conectado cuyo nombre empieza con `prefix` (ej. "CABLE Output").
        public static string Find(int flow, string prefix)
        {
            foreach (string[] device in List(flow))
            {
                if (device[1].StartsWith(prefix, StringComparison.OrdinalIgnoreCase)) return device[0];
            }
            return null;
        }

        // Una punta de VB-CABLE: flow 0 = "CABLE Input" (donde clonavoz pone tu
        // voz traducida), 1 = "CABLE Output" (el micrófono de la videollamada).
        // Si alguien le cambió el nombre, se la reconoce por el del driver.
        public static string FindCable(int flow)
        {
            string id = Find(flow, flow == 0 ? "CABLE Input" : "CABLE Output");
            if (id != null) return id;
            foreach (string[] device in List(flow))
            {
                if (device[1].IndexOf("VB-Audio Virtual Cable", StringComparison.OrdinalIgnoreCase) >= 0) return device[0];
            }
            return null;
        }

        public static bool CableInstalled()
        {
            return FindCable(0) != null && FindCable(1) != null;
        }

        // Una punta de VB-CABLE (y no otro dispositivo virtual, como los de Voicemeeter).
        public static bool IsCable(string name)
        {
            return name != null && (name.StartsWith("CABLE", StringComparison.OrdinalIgnoreCase)
                || name.IndexOf("VB-Audio Virtual Cable", StringComparison.OrdinalIgnoreCase) >= 0);
        }

        public static bool IsVirtual(string name)
        {
            return name != null && (name.StartsWith("CABLE", StringComparison.OrdinalIgnoreCase)
                || name.IndexOf("VB-Audio", StringComparison.OrdinalIgnoreCase) >= 0);
        }

        // Un micrófono de verdad (no un cable virtual), si hay alguno conectado.
        public static string FirstRealMicrophone()
        {
            foreach (string[] device in List(1))
            {
                if (!IsVirtual(device[1])) return device[0];
            }
            return null;
        }

        public static int Set(string id, int role)
        {
            try
            {
                return ((IPolicyConfig)new PolicyConfigClient()).SetDefaultEndpoint(id, role);
            }
            catch (Exception)
            {
                return -1;
            }
        }

        public static int Set(string id)
        {
            int result = 0;
            for (int role = 0; role < 3; role++)
            {
                int hr = Set(id, role);
                if (hr != 0) result = hr;
            }
            return result;
        }

        // --- Que se escuche: el silencio y el volumen de Windows.

        static IAudioEndpointVolume Volume(string id)
        {
            IMMDevice device;
            if (id == null || Enumerator().GetDevice(id, out device) != 0 || device == null) return null;
            var iid = new Guid("5CDF2C82-841E-4546-9722-0CF74078229A");
            IntPtr pointer;
            if (device.Activate(ref iid, 23, IntPtr.Zero, out pointer) != 0 || pointer == IntPtr.Zero) return null;  // CLSCTX_ALL
            try
            {
                return (IAudioEndpointVolume)Marshal.GetObjectForIUnknown(pointer);
            }
            finally
            {
                Marshal.Release(pointer);
            }
        }

        // Si el dispositivo está silenciado en Windows, o con el volumen casi en
        // cero (típico con la tecla de silenciar de las notebooks), lo arregla.
        // Devuelve qué arregló, o null si estaba bien (o no se pudo saber).
        public static string MakeAudible(string id)
        {
            try
            {
                IAudioEndpointVolume volume = Volume(id);
                if (volume == null) return null;
                var context = Guid.Empty;
                var fixes = new List<string>();
                bool muted;
                if (volume.GetMute(out muted) == 0 && muted && volume.SetMute(false, ref context) == 0)
                {
                    fixes.Add("estaba silenciado");
                }
                float level;
                if (volume.GetMasterVolumeLevelScalar(out level) == 0 && level < 0.1f
                    && volume.SetMasterVolumeLevelScalar(1f, ref context) == 0)
                {
                    fixes.Add("tenía el volumen en " + Math.Round(level * 100) + "%");
                }
                return fixes.Count > 0 ? string.Join(" y ", fixes.ToArray()) : null;
            }
            catch (Exception)
            {
                return null;
            }
        }

        // Si está silenciado (null si no se sabe). Y silenciarlo, para las pruebas.
        public static bool? IsMuted(string id)
        {
            try
            {
                IAudioEndpointVolume volume = Volume(id);
                bool muted;
                if (volume == null || volume.GetMute(out muted) != 0) return null;
                return muted;
            }
            catch (Exception)
            {
                return null;
            }
        }

        public static bool SetMute(string id, bool mute)
        {
            try
            {
                IAudioEndpointVolume volume = Volume(id);
                var context = Guid.Empty;
                return volume != null && volume.SetMute(mute, ref context) == 0;
            }
            catch (Exception)
            {
                return false;
            }
        }

        // Si tu salida de audio predeterminada es el cable virtual (así no
        // escuchás la PC ni la llamada, y la otra persona se escucharía a sí
        // misma), pone otra de verdad. Devuelve su nombre, o null si no hacía
        // falta (o no hay otra).
        public static string FixOutput()
        {
            string console = Get(0, 0);
            string calls = Get(0, 2);
            bool consoleCable = IsCable(Name(console));
            bool callsCable = IsCable(Name(calls));
            if (!consoleCable && !callsCable) return null;
            string real = BestOutput();
            if (real == null) return null;
            if (consoleCable && (Set(real, 0) != 0 || Set(real, 1) != 0)) return null;
            if (callsCable && Set(consoleCable ? real : console, 2) != 0) return null;
            return Name(real);
        }

        // Una salida de verdad: mejor auriculares, si no parlantes, si no cualquiera
        // (el HDMI de un monitor sin parlantes, al final).
        static string BestOutput()
        {
            string[][] preferences = new string[][]
            {
                new string[] { "auricular", "headphone", "headset", "casque", "kopfh", "fone" },
                new string[] { "altavoc", "speaker", "parlante", "alto-falante", "haut-parleur", "lautsprecher" },
                new string[] { "" },
            };
            foreach (string[] words in preferences)
            {
                foreach (string[] device in List(0))
                {
                    if (IsVirtual(device[1])) continue;
                    foreach (string word in words)
                    {
                        if (device[1].IndexOf(word, StringComparison.OrdinalIgnoreCase) >= 0) return device[0];
                    }
                }
            }
            return null;
        }

        // --- Para el instalador de VB-CABLE: al instalarlo, Windows a veces lo
        // deja como parlante y micrófono predeterminados (y dejás de escuchar la
        // PC). Se anotan los de antes y después se vuelven a poner.

        public static string[] Snapshot()
        {
            return new string[] { Get(0), Get(1) };
        }

        // Devuelve qué se volvió a poner: 1 = la salida, 2 = el micrófono (o los dos, 3).
        public static int RestoreSnapshot(string[] before)
        {
            int restored = 0;
            for (int flow = 0; flow < 2; flow++)
            {
                string old = before[flow];
                if (old != null && Get(flow) != old && IsActive(old) && Set(old) == 0) restored |= 1 << flow;
            }
            return restored;
        }

        // --- Mientras clonavoz traduce: "CABLE Output" como micrófono predeterminado.

        // Pone "CABLE Output" como micrófono predeterminado (para las apps que usan
        // el de consola y las de llamadas, que usan el de comunicaciones) y anota
        // en `saved` cuáles eran, para volver a ponerlos aunque clonavoz se cierre
        // mal. Devuelve el nombre de tu micrófono real ("" si no se sabe), o null
        // si no se pudo (no está VB-CABLE o Windows no dejó cambiarlo).
        public static string UseCable(string saved)
        {
            string cable = FindCable(1);
            if (cable == null) return null;
            string console = Get(1, 0);
            string communications = Get(1, 2);
            string real = null;
            if (console != null && console != cable)
            {
                string calls = communications != null && communications != cable ? communications : console;
                Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(saved)));
                File.WriteAllLines(saved, new string[] { console, calls }, new UTF8Encoding(false));
                real = Name(console);
            }
            else
            {
                // Ya estaba el cable (ej. clonavoz se cerró sin volver atrás): tu
                // micrófono es el anotado, o si no, cualquiera de verdad.
                string[] ids = ReadIds(saved);
                if (ids.Length > 0) real = Name(ids[0]);
                if (real == null) real = Name(FirstRealMicrophone());
            }
            if (Set(cable) != 0) return null;
            return real ?? "";
        }

        // Tu micrófono de verdad: el predeterminado si no es el cable; si no, el
        // anotado por UseCable; si no, cualquiera conectado.
        public static string RealMicrophone(string saved)
        {
            string console = Get(1, 0);
            if (console != null && !IsCable(Name(console))) return console;
            string[] ids = ReadIds(saved);
            if (ids.Length > 0 && IsActive(ids[0])) return ids[0];
            return FirstRealMicrophone();
        }

        // Vuelve a poner el micrófono de antes. Devuelve true si quedó como estaba
        // (o no había nada que volver atrás). Si tu micrófono de antes no está
        // conectado, por lo menos no deja el cable: pone otro de verdad, y lo
        // vuelve a intentar la próxima vez.
        public static bool Restore(string saved)
        {
            string[] ids = ReadIds(saved);
            if (ids.Length == 0)
            {
                if (File.Exists(saved)) File.Delete(saved);
                return true;
            }
            string console = ids[0];
            string communications = ids.Length > 1 ? ids[1] : ids[0];
            bool restored;
            if (IsActive(console))
            {
                restored = Set(console, 0) == 0 && Set(console, 1) == 0;
            }
            else
            {
                restored = false;
                string other = FirstRealMicrophone();
                if (other != null && IsVirtual(Name(Get(1, 0)))) Set(other);
            }
            if (IsActive(communications)) restored = Set(communications, 2) == 0 && restored;
            if (restored) File.Delete(saved);
            return restored;
        }

        static string[] ReadIds(string saved)
        {
            var ids = new List<string>();
            try
            {
                if (File.Exists(saved))
                {
                    foreach (string line in File.ReadAllLines(saved))
                    {
                        if (line.Trim().Length > 0) ids.Add(line.Trim());
                    }
                }
            }
            catch (Exception)
            {
                // si no se puede leer, es como si no hubiera nada anotado
            }
            return ids.ToArray();
        }
    }
}
