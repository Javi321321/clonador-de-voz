// clonavoz.exe: un solo archivo para llevar en el pendrive, con una ventana y
// un botón ACTIVAR que hace solo todo lo que se puede hacer solo.
//
// Adentro trae la versión portable entera (Python y todo lo que usa) como un
// .zip pegado al final del .exe. La primera vez que lo abrís se instala en la
// carpeta "clonavoz", al lado del .exe (en el mismo pendrive: nada se instala
// en la computadora). Si es una versión nueva, actualiza el programa y
// conserva tu voz y los modelos descargados (carpeta "datos").
//
// ACTIVAR, en orden: baja los modelos si faltan (una vez, con internet), graba
// tu voz si falta (15 segundos), ofrece instalar VB-CABLE si falta (con su
// instalador oficial, una vez por PC), pide el permiso para clonar la voz de
// los demás si elegiste eso, pone "CABLE Output" como micrófono predeterminado
// de Windows (así la videollamada lo usa sin elegir nada) y arranca la
// conversación: lo que te dicen aparece traducido en la ventana y suena en
// tus auriculares. DETENER (o cerrar la ventana) vuelve a poner tu micrófono.
//
// Se compila con el compilador de C# que trae Windows (C# 5), junto con
// windows/AudioDefaults.cs (ver portable/build_windows.py). Opciones:
//   --solo-instalar    instala (o actualiza) y sale, sin ventana
//   --carpeta <ruta>   dónde está (o dónde instalar) clonavoz
//   --menu             abre el menú de consola (Iniciar.bat) en vez de la ventana
//   --prueba           abre la ventana, cuenta lo que ve y se cierra sola
//   --prueba-activar   toca ACTIVAR sola, espera a que arranque y lo detiene
//                      (contesta "no" a todo lo que pregunta). Para las pruebas.
//   --captura <png>    con --prueba o --prueba-activar: guarda cómo se ve la ventana
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Windows.Forms;
using ClonavozAudio;
using Microsoft.Win32;

static class Launcher
{
    public const string Version = "__VERSION__";  // lo reemplaza build_windows.py

    static Mutex single;  // una sola ventana abierta a la vez

    [DllImport("user32.dll")]
    static extern bool SetProcessDPIAware();

    [STAThread]
    static int Main(string[] args)
    {
        string exe = Assembly.GetExecutingAssembly().Location;
        string here = Path.GetDirectoryName(Path.GetFullPath(exe));
        string app = File.Exists(Path.Combine(here, "Iniciar.bat")) ? here : Path.Combine(here, "clonavoz");
        bool installOnly = false;
        bool menu = false;
        string test = null;
        string capture = null;
        for (int i = 0; i < args.Length; i++)
        {
            if (args[i] == "--solo-instalar") installOnly = true;
            else if (args[i] == "--menu") menu = true;
            else if (args[i] == "--prueba" || args[i] == "--prueba-activar") test = args[i];
            else if (args[i] == "--carpeta" && i + 1 < args.Length) app = Path.GetFullPath(args[++i]);
            else if (args[i] == "--captura" && i + 1 < args.Length) capture = Path.GetFullPath(args[++i]);
        }
        if (installOnly) return InstallWithoutWindow(exe, app);

        TestOutput.Enabled = test != null;
        try
        {
            SetProcessDPIAware();  // letras nítidas en pantallas con zoom (125%, 150%)
        }
        catch (Exception)
        {
            // Windows muy viejo (o Mono): se ve igual, un poco borroso
        }
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        Application.SetUnhandledExceptionMode(UnhandledExceptionMode.CatchException);
        Application.ThreadException += delegate(object sender, ThreadExceptionEventArgs e) { Crashed(e.Exception, app, test); };
        AppDomain.CurrentDomain.UnhandledException += delegate(object sender, UnhandledExceptionEventArgs e)
        {
            Crashed(e.ExceptionObject as Exception, app, test);
        };
        try
        {
            if (test == null && Installer.NeedsInstall(exe, app) && InsideTemporaryFolder(here))
            {
                Show("Estás abriendo clonavoz.exe desde adentro del .zip descargado (o desde una carpeta temporal): " +
                     "Windows lo borraría después.\n\nPrimero copiá clonavoz.exe al pendrive (o a una carpeta de tu PC) y " +
                     "abrilo desde ahí.");
                return 1;
            }
            if (Installer.NeedsInstall(exe, app) && !InstallForm.Install(exe, app)) return 1;
            if (!Installer.IsInstalled(app))
            {
                Show("No se encontró clonavoz en " + app + " y este .exe no trae el programa adentro.\n\n" +
                     "Descargá clonavoz.exe completo (el de unos 450 MB) y ponelo en el pendrive.");
                return 1;
            }
            if (menu)
            {
                MainForm.OpenConsoleMenu(app);
                return 0;
            }
            if (test == null && AlreadyOpen())
            {
                Show("clonavoz ya está abierto: buscalo en la barra de tareas.");
                return 0;
            }
            var form = new MainForm(app, test);
            form.CapturePath = capture;
            Application.Run(form);
            GC.KeepAlive(single);
            return form.ExitCode;
        }
        catch (Exception exc)
        {
            TestOutput.Line("ERROR: " + exc);
            if (test == null) Show("Error: " + exc.Message);
            return 1;
        }
    }

    // Un error que no se esperaba: que no quede CABLE Output como tu micrófono.
    static void Crashed(Exception exc, string app, string test)
    {
        TestOutput.Line("ERROR: " + exc);
        try
        {
            Defaults.Restore(Path.Combine(app, "datos", "microfono_anterior.txt"));
        }
        catch (Exception)
        {
        }
        if (test != null) Environment.Exit(1);
        MessageBox.Show("Algo falló en la ventana de clonavoz:\n\n" + (exc != null ? exc.Message : "?") +
                        "\n\nSi vuelve a pasar, usá \"Más opciones (menú)\".", "clonavoz",
                        MessageBoxButtons.OK, MessageBoxIcon.Error);
    }

    // Al abrir un .exe desde adentro de un .zip, Windows lo copia a la carpeta temporal.
    static bool InsideTemporaryFolder(string folder)
    {
        try
        {
            string temp = Path.GetFullPath(Path.GetTempPath()).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
            return (Path.GetFullPath(folder) + Path.DirectorySeparatorChar).StartsWith(temp, StringComparison.OrdinalIgnoreCase);
        }
        catch (Exception)
        {
            return false;
        }
    }

    static bool AlreadyOpen()
    {
        try
        {
            bool created;
            single = new Mutex(true, "Local\\clonavoz-ventana", out created);
            return !created;
        }
        catch (Exception)
        {
            return false;
        }
    }

    public static void Show(string text)
    {
        TestOutput.Line("AVISO: " + text);
        if (!TestOutput.Enabled) MessageBox.Show(text, "clonavoz", MessageBoxButtons.OK, MessageBoxIcon.Warning);
    }

    static int InstallWithoutWindow(string exe, string app)
    {
        TestOutput.Enabled = true;
        try
        {
            if (Installer.IsCurrent(app))
            {
                TestOutput.Line("Ya está instalado en " + app);
                return 0;
            }
            if (!Installer.HasPayload(exe))
            {
                TestOutput.Line("Este .exe no trae el programa adentro.");
                return 1;
            }
            TestOutput.Line("Instalando clonavoz en " + app + "...");
            string warning = Installer.SpaceWarning(exe, app);
            if (warning != null) TestOutput.Line("AVISO: " + warning);
            int shown = -10;
            Installer.Install(exe, app, delegate(int percent)
            {
                if (percent < shown + 10) return;
                shown = percent;
                TestOutput.Line(percent + "%");
            });
            TestOutput.Line("Listo.");
            return 0;
        }
        catch (Exception exc)
        {
            TestOutput.Line("ERROR: " + exc.Message);
            return 1;
        }
    }
}

// Lo que cuenta la ventana en las pruebas (--prueba, --solo-instalar): sale
// por la salida estándar, si alguien la está leyendo (ej. `clonavoz.exe
// --prueba > registro.txt`).
static class TestOutput
{
    public static bool Enabled;
    static readonly object Lock = new object();
    static StreamWriter writer;

    public static void Line(string text)
    {
        if (!Enabled) return;
        lock (Lock)
        {
            try
            {
                if (writer == null)
                {
                    writer = new StreamWriter(Console.OpenStandardOutput(), new UTF8Encoding(false));
                    writer.AutoFlush = true;
                }
                writer.WriteLine(text);
            }
            catch (Exception)
            {
                // nadie la lee: da igual
            }
        }
    }
}

static class Installer
{
    public static bool IsInstalled(string app)
    {
        return File.Exists(Path.Combine(app, "Iniciar.bat")) && File.Exists(Path.Combine(app, "python", "python.exe"));
    }

    public static bool IsCurrent(string app)
    {
        string version = Path.Combine(app, "version.txt");
        return IsInstalled(app) && File.Exists(version) && File.ReadAllText(version).Trim() == Launcher.Version;
    }

    public static bool HasPayload(string exe)
    {
        try
        {
            using (ZipArchive zip = ZipFile.OpenRead(exe))
            {
                return zip.Entries.Count > 0;
            }
        }
        catch (InvalidDataException)
        {
            return false;
        }
    }

    // Hay que instalar (o actualizar) si este .exe trae el programa y en la
    // carpeta no está esta misma versión.
    public static bool NeedsInstall(string exe, string app)
    {
        return !IsCurrent(app) && HasPayload(exe);
    }

    public static string SpaceWarning(string exe, string app)
    {
        try
        {
            long needed = 3L * 1024 * 1024 * 1024;  // los modelos que se bajan después (~2.7 GB)
            using (ZipArchive zip = ZipFile.OpenRead(exe))
            {
                foreach (ZipArchiveEntry entry in zip.Entries) needed += entry.Length;
            }
            var drive = new DriveInfo(Path.GetPathRoot(Path.GetFullPath(app)));
            if (drive.AvailableFreeSpace >= needed) return null;
            return string.Format(
                "Quedan {0:0.0} GB libres y hacen falta unos {1:0.0} GB (el programa y los modelos).",
                drive.AvailableFreeSpace / 1073741824.0, needed / 1073741824.0);
        }
        catch (Exception)
        {
            return null;  // si no se puede saber, se intenta igual
        }
    }

    // Copia el programa que trae el .exe a `app`. Tu voz, los modelos y lo que
    // elegiste (carpeta "datos") se conservan. `progress` recibe de 0 a 100.
    public static void Install(string exe, string app, Action<int> progress)
    {
        using (ZipArchive zip = ZipFile.OpenRead(exe))
        {
            long total = 0;
            foreach (ZipArchiveEntry entry in zip.Entries) total += entry.Length;
            Directory.CreateDirectory(app);
            string python = Path.Combine(app, "python");
            if (Directory.Exists(python))
            {
                Directory.Delete(python, true);  // el Python de la versión anterior
            }
            long done = 0;
            int shown = -1;
            string root = Path.GetFullPath(app).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
            foreach (ZipArchiveEntry entry in zip.Entries)
            {
                string destination = Path.GetFullPath(Path.Combine(app, entry.FullName.Replace('/', Path.DirectorySeparatorChar)));
                if (!destination.StartsWith(root, StringComparison.OrdinalIgnoreCase))
                {
                    throw new InvalidDataException("El paquete tiene una ruta inválida: " + entry.FullName);
                }
                if (entry.FullName.EndsWith("/"))
                {
                    Directory.CreateDirectory(destination);
                    continue;
                }
                bool userData = entry.FullName.StartsWith("datos/", StringComparison.OrdinalIgnoreCase);
                if (!(userData && File.Exists(destination)))
                {
                    Directory.CreateDirectory(Path.GetDirectoryName(destination));
                    entry.ExtractToFile(destination, true);
                }
                done += entry.Length;
                int percent = total > 0 ? (int)(done * 100 / total) : 100;
                if (percent != shown)
                {
                    shown = percent;
                    progress(percent);
                }
            }
            File.WriteAllText(Path.Combine(app, "version.txt"), Launcher.Version + Environment.NewLine);
        }
    }
}

// La ventanita de la primera vez (o de una actualización), mientras se copia el programa.
sealed class InstallForm : Form
{
    readonly string exe;
    readonly string app;
    readonly ProgressBar bar = new ProgressBar();
    Exception error;
    bool done;

    public static bool Install(string exe, string app)
    {
        string warning = Installer.SpaceWarning(exe, app);
        if (warning != null) Launcher.Show(warning + "\n\nSe intenta igual.");
        using (var form = new InstallForm(exe, app))
        {
            form.ShowDialog();
            if (form.error != null)
            {
                Launcher.Show("No se pudo instalar clonavoz en " + app + ":\n\n" + form.error.Message +
                              "\n\n¿Hay lugar libre y se puede escribir en esa carpeta?");
                return false;
            }
            return form.done;
        }
    }

    InstallForm(string exe, string app)
    {
        this.exe = exe;
        this.app = app;
        bool update = Directory.Exists(app);
        float scale = MainForm.DpiScale(this);
        Text = "clonavoz";
        Font = new Font("Segoe UI", 10f);
        FormBorderStyle = FormBorderStyle.FixedDialog;
        ControlBox = false;
        StartPosition = FormStartPosition.CenterScreen;
        ClientSize = new Size((int)(500 * scale), (int)(150 * scale));

        var layout = new TableLayoutPanel();
        layout.Dock = DockStyle.Fill;
        layout.ColumnCount = 1;
        layout.Padding = new Padding((int)(16 * scale));
        var title = new Label();
        title.AutoSize = true;
        title.Font = new Font("Segoe UI", 12f, FontStyle.Bold);
        title.Text = update ? "Actualizando clonavoz (tu voz y los modelos se conservan)..."
                            : "Instalando clonavoz en el pendrive (una sola vez)...";
        var folder = new Label();
        folder.AutoSize = true;
        folder.ForeColor = Color.DimGray;
        folder.Text = "Carpeta: " + app + "\nTodo queda ahí: en esta computadora no se instala nada.";
        bar.Dock = DockStyle.Fill;
        bar.Height = (int)(22 * scale);
        layout.Controls.Add(title);
        layout.Controls.Add(bar);
        layout.Controls.Add(folder);
        Controls.Add(layout);
        Shown += delegate
        {
            var worker = new Thread(Work);
            worker.IsBackground = true;
            worker.Start();
        };
    }

    void Work()
    {
        try
        {
            Installer.Install(exe, app, delegate(int percent)
            {
                BeginInvoke((Action)delegate { bar.Value = Math.Min(100, Math.Max(0, percent)); });
            });
            done = true;
        }
        catch (Exception exc)
        {
            error = exc;
        }
        BeginInvoke((Action)Close);
    }
}

// Lo que elegiste: los mismos archivos que usa el menú (Iniciar.bat), en la carpeta "datos".
sealed class Settings
{
    public string Speak = "es";           // idioma en que hablás
    public string HeardIn = "auto";       // en qué idioma te escuchan ("auto": el de quien te habla)
    public string ListenIn;               // en qué idioma escuchás lo que te dicen
    public string TheirLangs = "en pt";   // idiomas en que es más probable que te hablen
    public string MyVoice = "auto";       // auto, natural (siempre clonada) o rapida
    public string TheirVoice = "parecida";  // parecida, clonada (con su permiso) o ninguna
    public bool AutoStart;                // ACTIVAR solo al abrir la ventana
    public bool OnTop;                    // ventana siempre visible
    readonly string datos;

    public Settings(string datos)
    {
        this.datos = datos;
        string[] words = Words("idiomas.txt");
        if (words.Length >= 1) Speak = words[0];
        if (words.Length >= 2) HeardIn = words[1];
        words = Words("escucho.txt");
        ListenIn = words.Length >= 1 ? words[0] : Speak;
        words = Words("sus_idiomas.txt");
        if (words.Length >= 1) TheirLangs = string.Join(" ", words);
        words = Words("voz.txt");
        if (words.Length >= 1) MyVoice = words[0];
        words = Words("su_voz.txt");
        if (words.Length >= 1) TheirVoice = words[0];
        foreach (string word in Words("ventana.txt"))
        {
            if (word == "activar_al_abrir") AutoStart = true;
            if (word == "siempre_visible") OnTop = true;
        }
    }

    string[] Words(string name)
    {
        try
        {
            string path = Path.Combine(datos, name);
            if (!File.Exists(path)) return new string[0];
            string[] lines = File.ReadAllLines(path);
            if (lines.Length == 0) return new string[0];
            return lines[0].Split(new char[] { ' ', '\t' }, StringSplitOptions.RemoveEmptyEntries);
        }
        catch (Exception)
        {
            return new string[0];
        }
    }

    public void Save()
    {
        try
        {
            Directory.CreateDirectory(datos);
            Write("idiomas.txt", Speak + " " + HeardIn);
            Write("escucho.txt", ListenIn);
            Write("sus_idiomas.txt", TheirLangs);
            Write("voz.txt", MyVoice);
            Write("su_voz.txt", TheirVoice);
            Write("ventana.txt", (AutoStart ? "activar_al_abrir " : "") + (OnTop ? "siempre_visible" : ""));
        }
        catch (Exception)
        {
            // pendrive protegido contra escritura: se usa igual, sin guardar
        }
    }

    void Write(string name, string value)
    {
        File.WriteAllText(Path.Combine(datos, name), value + "\r\n", Encoding.ASCII);
    }

    // Con "auto", hasta que te hablen te escuchan en inglés (como el menú).
    public string Fixed
    {
        get { return HeardIn == "auto" ? "en" : HeardIn; }
    }

    // Los idiomas que hay que tener descargados.
    public List<string> Languages()
    {
        var all = new List<string>();
        var candidates = new List<string> { Speak, Fixed };
        candidates.AddRange(TheirLangs.Split(new char[] { ' ' }, StringSplitOptions.RemoveEmptyEntries));
        candidates.Add(ListenIn);
        foreach (string code in candidates)
        {
            if (!all.Contains(code)) all.Add(code);
        }
        return all;
    }
}

// Corre clonavoz.bat con unos argumentos, sin ventana de consola, y pasa cada
// línea que escribe. Detenerlo corta clonavoz (y lo que haya abierto) al instante.
sealed class Runner
{
    readonly string app;
    readonly Action<string, bool> line;  // (texto, si salió por la salida de errores)
    Process process;

    public Runner(string app, Action<string, bool> line)
    {
        this.app = app;
        this.line = line;
    }

    // `token`: el de Hugging Face para bajar la voz natural (solo para esta
    // descarga: va en una variable de entorno de este proceso, no se guarda).
    public void Start(string arguments, string token, Action<int> exited)
    {
        Stop();
        string bat = Path.Combine(app, "clonavoz.bat");
        var start = new ProcessStartInfo("cmd.exe", "/d /c \"\"" + bat + "\" " + arguments + "\"");
        start.UseShellExecute = false;
        start.CreateNoWindow = true;
        start.WorkingDirectory = app;
        start.RedirectStandardInput = true;
        start.RedirectStandardOutput = true;
        start.RedirectStandardError = true;
        start.StandardOutputEncoding = new UTF8Encoding(false);  // clonavoz.bat pone PYTHONUTF8=1
        start.StandardErrorEncoding = new UTF8Encoding(false);
        start.EnvironmentVariables["PYTHONUNBUFFERED"] = "1";
        start.EnvironmentVariables["CLONAVOZ_ESTADO"] = "1";  // el estado en líneas "@@ ..."
        start.EnvironmentVariables["CLONAVOZ_PADRE"] = Process.GetCurrentProcess().Id.ToString();
        if (!string.IsNullOrEmpty(token)) start.EnvironmentVariables["HF_TOKEN"] = token;
        var p = new Process();
        p.StartInfo = start;
        p.OutputDataReceived += delegate(object sender, DataReceivedEventArgs e) { if (e.Data != null) line(e.Data, false); };
        p.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs e) { if (e.Data != null) line(e.Data, true); };
        p.Start();
        process = p;
        try
        {
            p.StandardInput.Close();  // nada de preguntas por consola: las hace la ventana
        }
        catch (IOException)
        {
        }
        p.BeginOutputReadLine();
        p.BeginErrorReadLine();
        var watcher = new Thread(delegate()
        {
            p.WaitForExit();  // espera también a que termine de leer lo que escribió
            int code;
            try
            {
                code = p.ExitCode;
            }
            catch (InvalidOperationException)
            {
                code = -1;
            }
            exited(code);
        });
        watcher.IsBackground = true;
        watcher.Start();
    }

    public void Stop()
    {
        Process p = process;
        process = null;
        if (p == null) return;
        try
        {
            if (p.HasExited) return;
            var kill = new ProcessStartInfo("taskkill.exe", "/F /T /PID " + p.Id);
            kill.UseShellExecute = false;
            kill.CreateNoWindow = true;
            using (Process killer = Process.Start(kill))
            {
                killer.WaitForExit(10000);
            }
            if (!p.WaitForExit(5000)) p.Kill();
        }
        catch (Exception)
        {
            // ya había terminado
        }
    }
}

// La ventana principal.
sealed class MainForm : Form
{
    enum State { Idle, Preparing, Starting, Active }

    static readonly string[][] LanguageNames = new string[][]
    {
        new string[] { "es", "Español" }, new string[] { "en", "Inglés" }, new string[] { "pt", "Portugués" },
        new string[] { "fr", "Francés" }, new string[] { "it", "Italiano" }, new string[] { "de", "Alemán" },
    };
    static readonly Color Green = Color.FromArgb(21, 128, 61);
    static readonly Color Red = Color.FromArgb(185, 28, 28);
    static readonly Color Amber = Color.FromArgb(180, 83, 9);
    static readonly Color Slate = Color.FromArgb(71, 85, 105);
    static readonly Color Blue = Color.FromArgb(30, 64, 175);
    static readonly Color Gray = Color.FromArgb(115, 115, 115);

    const string ConsentText =
        "Elegiste escuchar a los demás con SU voz clonada. Eso requiere su permiso: avisale a la otra " +
        "persona que su voz se va a clonar para traducirte lo que dice (solo suena en tus auriculares y " +
        "no se guarda) y pedile que acepte.\n\n¿La persona con la que vas a hablar te dio permiso?\n\n" +
        "(Si no, se usa una voz parecida: de hombre o de mujer, según su tono.)";
    const string CableQuestion =
        "Para que te escuchen traducido en la videollamada hace falta el micrófono virtual VB-CABLE " +
        "(gratis, de VB-Audio Software: www.vb-cable.com; es donationware).\n\n" +
        "Viene incluido, tal cual lo publica su autor. Se instala una sola vez en esta PC con su " +
        "instalador oficial, que pide permiso de administrador: tocá \"Install Driver\".\n\n" +
        "¿Instalarlo ahora?\n\n(Si elegís No, igual te traduzco lo que te dicen.)";

    public int ExitCode;
    public string CapturePath;  // en las pruebas: dónde guardar cómo se ve
    readonly string app;
    readonly string datos;
    readonly string test;
    readonly Settings settings;
    readonly Runner runner;
    readonly float scale;
    State state = State.Idle;
    int generation;        // cambia con cada ACTIVAR o DETENER: lo que quedó de antes no sigue
    bool micSwitched;      // se puso CABLE Output como micrófono predeterminado
    bool askedCable;       // ya se ofreció instalar VB-CABLE (no insistir)
    bool conversing;       // "conversar" (con VB-CABLE) o solo "escuchar"
    bool wasActive;
    string heardNow;       // en qué idioma te están escuchando ahora
    string lastSpeaker = "";
    readonly List<string> recent = new List<string>();  // las últimas líneas, para explicar un error

    Label status;
    Label reading;
    Label meter;
    Button activate;
    Button recordButton;
    Button testButton;
    LinkLabel cableLink;
    RichTextBox log;
    ComboBox speakBox;
    ComboBox heardBox;
    ComboBox listenBox;
    ComboBox myVoiceBox;
    ComboBox theirVoiceBox;
    readonly List<Control> settingControls = new List<Control>();
    Font fontSaid;
    Font fontForYou;
    Font fontMine;
    Font fontMineTranslated;
    Font fontInfo;
    Font fontError;

    string ReadyFile { get { return Path.Combine(datos, "modelos_listos.txt"); } }
    string VoiceFile { get { return Path.Combine(datos, "mi_voz.wav"); } }
    string SavedMic { get { return Path.Combine(datos, "microfono_anterior.txt"); } }

    public MainForm(string app, string test)
    {
        this.app = app;
        this.test = test;
        datos = Path.Combine(app, "datos");
        settings = new Settings(datos);
        runner = new Runner(app, OnLine);
        scale = DpiScale(this);
        BuildWindow();
    }

    public static float DpiScale(Control control)
    {
        try
        {
            using (Graphics g = control.CreateGraphics())
            {
                return Math.Max(1f, g.DpiX / 96f);
            }
        }
        catch (Exception)
        {
            return 1f;
        }
    }

    int S(int pixels)
    {
        return (int)Math.Round(pixels * scale);
    }

    // --- La ventana ---

    void BuildWindow()
    {
        Text = "clonavoz: traductor de voz con tu propia voz";
        Font = new Font("Segoe UI", 10f);
        BackColor = Color.White;
        StartPosition = FormStartPosition.CenterScreen;
        ClientSize = new Size(S(820), S(680));
        MinimumSize = new Size(S(620), S(520));
        fontSaid = new Font("Segoe UI", 10f);
        fontForYou = new Font("Segoe UI", 14f, FontStyle.Bold);
        fontMine = new Font("Segoe UI", 9.5f);
        fontMineTranslated = new Font("Segoe UI", 10.5f);
        fontInfo = new Font("Segoe UI", 9f, FontStyle.Italic);
        fontError = new Font("Segoe UI", 10f, FontStyle.Bold);

        var root = new TableLayoutPanel();
        root.Dock = DockStyle.Fill;
        root.ColumnCount = 1;
        root.Padding = new Padding(S(14), S(10), S(14), S(8));
        root.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100f));

        var title = new Label();
        title.Text = "clonavoz";
        title.Font = new Font("Segoe UI", 18f, FontStyle.Bold);
        title.ForeColor = Color.FromArgb(15, 23, 42);
        title.AutoSize = true;
        AddRow(root, title, SizeType.AutoSize, 0);

        status = new Label();
        status.AutoSize = false;
        status.Dock = DockStyle.Fill;
        status.Height = S(58);
        status.Font = new Font("Segoe UI", 10.5f);
        status.ForeColor = Color.FromArgb(30, 41, 59);
        AddRow(root, status, SizeType.AutoSize, 0);

        activate = new Button();
        activate.Dock = DockStyle.Fill;
        activate.FlatStyle = FlatStyle.Flat;
        activate.FlatAppearance.BorderSize = 0;
        activate.ForeColor = Color.White;
        activate.Font = new Font("Segoe UI", 20f, FontStyle.Bold);
        activate.Cursor = Cursors.Hand;
        activate.Click += delegate { if (state == State.Idle) StartActivation(); else StopEverything(true); };
        AddRow(root, activate, SizeType.Absolute, S(72));

        AddRow(root, BuildSettings(), SizeType.AutoSize, 0);

        reading = new Label();
        reading.AutoSize = false;
        reading.Dock = DockStyle.Fill;
        reading.Height = S(130);
        reading.Visible = false;
        reading.Font = new Font("Segoe UI", 13f);
        reading.BackColor = Color.FromArgb(254, 249, 195);
        reading.Padding = new Padding(S(10));
        AddRow(root, reading, SizeType.AutoSize, 0);

        log = new RichTextBox();
        log.Dock = DockStyle.Fill;
        log.ReadOnly = true;
        log.BackColor = Color.FromArgb(248, 250, 252);
        log.BorderStyle = BorderStyle.None;
        log.DetectUrls = false;
        log.HideSelection = false;
        AddRow(root, log, SizeType.Percent, 100);

        meter = new Label();
        meter.AutoSize = false;
        meter.Dock = DockStyle.Fill;
        meter.Height = S(24);
        meter.AutoEllipsis = true;
        meter.Font = new Font("Consolas", 9.5f);
        meter.ForeColor = Gray;
        AddRow(root, meter, SizeType.AutoSize, 0);

        AddRow(root, BuildButtons(), SizeType.AutoSize, 0);
        Controls.Add(root);
        FormClosing += delegate { StopEverything(false); };
        SetState(State.Idle);
    }

    static void AddRow(TableLayoutPanel table, Control control, SizeType size, float height)
    {
        table.RowStyles.Add(size == SizeType.AutoSize ? new RowStyle(SizeType.AutoSize) : new RowStyle(size, height));
        table.Controls.Add(control, 0, table.RowStyles.Count - 1);
    }

    Control BuildSettings()
    {
        var grid = new TableLayoutPanel();
        grid.Dock = DockStyle.Fill;
        grid.AutoSize = true;
        grid.ColumnCount = 4;
        grid.Padding = new Padding(0, S(8), 0, S(6));
        grid.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
        grid.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 50f));
        grid.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
        grid.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 50f));

        speakBox = Choices(LanguageChoices(null, settings.Speak), settings.Speak);
        heardBox = Choices(LanguageChoices("Automático (su idioma)", settings.HeardIn), settings.HeardIn);
        listenBox = Choices(LanguageChoices(null, settings.ListenIn), settings.ListenIn);
        myVoiceBox = Choices(new string[][]
        {
            new string[] { "auto", "Automática" },
            new string[] { "natural", "Siempre clonada" },
            new string[] { "rapida", "Rápida (sin clonar)" },
        }, settings.MyVoice);
        theirVoiceBox = Choices(new string[][]
        {
            new string[] { "parecida", "Parecida (hombre o mujer)" },
            new string[] { "clonada", "Su voz clonada (con permiso)" },
            new string[] { "ninguna", "Solo subtítulos" },
        }, settings.TheirVoice);

        var tips = new ToolTip();
        tips.AutoPopDelay = 20000;
        AddSetting(grid, 0, 0, "Hablo en:", speakBox, tips, "El idioma en que hablás vos.");
        AddSetting(grid, 0, 2, "Me escuchan en:", heardBox, tips,
                   "En qué idioma escuchan tu voz traducida. Automático: en el idioma en que te hablen\n" +
                   "(hasta que te hablen, en inglés).");
        AddSetting(grid, 1, 0, "Lo que me dicen, en:", listenBox, tips,
                   "En qué idioma querés leer y escuchar lo que te dicen.");
        AddSetting(grid, 1, 2, "Mi voz:", myVoiceBox, tips,
                   "Automática: tu voz clonada si esta PC llega a generarla a tiempo; si no, la rápida.\n" +
                   "Siempre clonada: tu voz, aunque en una PC lenta tarde más.\n" +
                   "Rápida: una voz de tono parecido al tuyo, sin clonar (la más rápida).");
        AddSetting(grid, 2, 0, "La voz de los demás:", theirVoiceBox, tips,
                   "Con qué voz escuchás lo que te dicen, traducido:\n" +
                   "Parecida: de hombre o de mujer, según su tono (no es su voz).\n" +
                   "Su voz clonada: solo con permiso de la otra persona (se pregunta cada vez).\n" +
                   "Solo subtítulos: sin voz, solo el texto en la ventana.");

        var onTop = new CheckBox();
        onTop.Text = "Ventana siempre visible (encima de la videollamada)";
        onTop.AutoSize = true;
        onTop.Anchor = AnchorStyles.Left;
        onTop.Checked = settings.OnTop;
        TopMost = settings.OnTop;
        onTop.CheckedChanged += delegate
        {
            TopMost = onTop.Checked;
            settings.OnTop = onTop.Checked;
            settings.Save();
        };
        grid.Controls.Add(onTop, 2, 2);
        grid.SetColumnSpan(onTop, 2);

        var autoStart = new CheckBox();
        autoStart.Text = "Activar solo al abrir clonavoz";
        autoStart.AutoSize = true;
        autoStart.Anchor = AnchorStyles.Left;
        autoStart.Checked = settings.AutoStart;
        autoStart.CheckedChanged += delegate
        {
            settings.AutoStart = autoStart.Checked;
            settings.Save();
        };
        tips.SetToolTip(autoStart, "Al abrir la ventana, hace lo mismo que tocar ACTIVAR.");
        grid.Controls.Add(autoStart, 0, 3);
        grid.SetColumnSpan(autoStart, 2);

        foreach (ComboBox box in new ComboBox[] { speakBox, heardBox, listenBox, myVoiceBox, theirVoiceBox })
        {
            box.SelectedIndexChanged += delegate { ReadSettings(); };
        }
        return grid;
    }

    void AddSetting(TableLayoutPanel grid, int row, int column, string text, ComboBox box, ToolTip tips, string tip)
    {
        var label = new Label();
        label.Text = text;
        label.AutoSize = true;
        label.Anchor = AnchorStyles.Left;
        label.Margin = new Padding(0, S(4), S(6), S(4));
        grid.Controls.Add(label, column, row);
        box.Dock = DockStyle.Fill;
        box.Margin = new Padding(0, S(3), S(16), S(3));
        grid.Controls.Add(box, column + 1, row);
        settingControls.Add(box);
        tips.SetToolTip(label, tip);
        tips.SetToolTip(box, tip);
    }

    static string[][] LanguageChoices(string automatic, string current)
    {
        var choices = new List<string[]>();
        if (automatic != null) choices.Add(new string[] { "auto", automatic });
        bool known = current == "auto" && automatic != null;
        foreach (string[] language in LanguageNames)
        {
            choices.Add(language);
            if (language[0] == current) known = true;
        }
        if (!known && !string.IsNullOrEmpty(current)) choices.Add(new string[] { current, current });  // elegido en el menú
        return choices.ToArray();
    }

    static string LanguageName(string code)
    {
        foreach (string[] language in LanguageNames)
        {
            if (language[0] == code) return language[1].ToLowerInvariant();
        }
        return code;
    }

    sealed class Choice
    {
        public string Code;
        public string Text;

        public override string ToString()
        {
            return Text;
        }
    }

    static ComboBox Choices(string[][] options, string current)
    {
        var box = new ComboBox();
        box.DropDownStyle = ComboBoxStyle.DropDownList;
        foreach (string[] option in options)
        {
            var choice = new Choice();
            choice.Code = option[0];
            choice.Text = option[1];
            box.Items.Add(choice);
            if (option[0] == current) box.SelectedIndex = box.Items.Count - 1;
        }
        if (box.SelectedIndex < 0) box.SelectedIndex = 0;
        return box;
    }

    static string Code(ComboBox box)
    {
        var choice = box.SelectedItem as Choice;
        return choice != null ? choice.Code : "";
    }

    void ReadSettings()
    {
        settings.Speak = Code(speakBox);
        settings.HeardIn = Code(heardBox);
        settings.ListenIn = Code(listenBox);
        settings.MyVoice = Code(myVoiceBox);
        settings.TheirVoice = Code(theirVoiceBox);
        settings.Save();
        if (state == State.Idle) status.Text = IdleText();
    }

    Control BuildButtons()
    {
        var row = new FlowLayoutPanel();
        row.Dock = DockStyle.Fill;
        row.AutoSize = true;
        row.WrapContents = true;
        row.Padding = new Padding(0, S(4), 0, 0);

        recordButton = SmallButton("Grabar mi voz de nuevo");
        recordButton.Click += delegate { RunTask(RecordAgain); };
        testButton = SmallButton("Probar micrófono");
        testButton.Click += delegate { RunTask(TestMicrophone); };
        var help = SmallButton("Cómo se usa");
        help.Click += delegate { OpenFile(Path.Combine(app, "LEEME.txt")); };
        var more = SmallButton("Más opciones (menú)");
        more.Click += delegate { OpenConsoleMenu(app); };
        cableLink = new LinkLabel();
        cableLink.AutoSize = true;
        cableLink.Margin = new Padding(S(8), S(9), S(4), 0);
        cableLink.LinkClicked += delegate { if (state == State.Idle) RunTask(InstallCableAsked); };

        row.Controls.Add(recordButton);
        row.Controls.Add(testButton);
        row.Controls.Add(help);
        row.Controls.Add(more);
        row.Controls.Add(cableLink);
        return row;
    }

    Button SmallButton(string text)
    {
        var button = new Button();
        button.Text = text;
        button.AutoSize = true;
        button.Padding = new Padding(S(4), S(2), S(4), S(2));
        button.Margin = new Padding(0, S(2), S(6), S(2));
        return button;
    }

    // --- Estados ---

    void SetState(State next)
    {
        state = next;
        bool idle = next == State.Idle;
        foreach (Control control in settingControls) control.Enabled = idle;
        recordButton.Enabled = idle;
        testButton.Enabled = idle;
        KeepAwake(next == State.Active || next == State.Starting);
        Text = next == State.Active ? "clonavoz: ACTIVO" : "clonavoz: traductor de voz con tu propia voz";
        if (next == State.Idle)
        {
            activate.Text = "ACTIVAR";
            activate.BackColor = Green;
            status.Text = IdleText();
            meter.Text = "";
            reading.Visible = false;
            RefreshCable();
        }
        else if (next == State.Preparing)
        {
            activate.Text = "CANCELAR";
            activate.BackColor = Amber;
        }
        else if (next == State.Starting)
        {
            activate.Text = "DETENER";
            activate.BackColor = Red;
            status.Text = "Arrancando: cargar los modelos tarda unos segundos (la primera vez, más).";
        }
        else
        {
            wasActive = true;
            activate.Text = "DETENER";
            activate.BackColor = Red;
            status.Text = ActiveText();
            TestOutput.Line("ACTIVO");
            if (test == "--prueba-activar") StopAfterTest();
        }
    }

    [DllImport("kernel32.dll")]
    static extern uint SetThreadExecutionState(uint flags);

    // Que la PC no se suspenda en medio de una conversación.
    static void KeepAwake(bool awake)
    {
        try
        {
            SetThreadExecutionState(awake ? 0x80000001u : 0x80000000u);  // ES_CONTINUOUS (+ ES_SYSTEM_REQUIRED)
        }
        catch (Exception)
        {
            // fuera de Windows
        }
    }

    string IdleText()
    {
        string text = "Tocá ACTIVAR y clonavoz prepara todo solo (te avisa si necesita algo). ";
        if (!File.Exists(ReadyFile)) text += "La primera vez baja los modelos (unos 2.7 GB, con internet) y graba tu voz (15 segundos).";
        else if (!File.Exists(VoiceFile)) text += "Primero va a grabar tu voz: 15 segundos.";
        else text += "Hablás en " + LanguageName(settings.Speak) + " y te escuchan en " + HeardText() + ".";
        return text;
    }

    string HeardText()
    {
        if (heardNow != null) return heardNow;
        return settings.HeardIn == "auto" ? "el idioma en que te hablen (hasta entonces, " + LanguageName(settings.Fixed) + ")"
                                         : LanguageName(settings.HeardIn);
    }

    string ActiveText()
    {
        if (!conversing)
        {
            return "ACTIVO: lo que suene en esta PC (la llamada, un video) aparece traducido acá" +
                   (settings.TheirVoice == "ninguna" ? "" : " y suena en tus auriculares") +
                   ". Sin VB-CABLE, a vos te escuchan con tu voz de siempre, sin traducir.";
        }
        return "ACTIVO: hablá normalmente, te escuchan en " + HeardText() + " con tu voz. En la videollamada el " +
               "micrófono es \"CABLE Output\"" + (micSwitched ? " (ya quedó como predeterminado)" : " (elegilo en la app)") +
               ". Lo que te dicen aparece acá y suena en tus auriculares.";
    }

    void RefreshCable()
    {
        bool installed = CableReady();
        cableLink.Text = installed ? "VB-CABLE: instalado (reinstalar o quitar)" : "VB-CABLE: falta en esta PC (instalar)";
        cableLink.LinkColor = installed ? Gray : Red;
    }

    static bool CableReady()
    {
        try
        {
            return Defaults.CableInstalled();
        }
        catch (Exception)
        {
            return false;  // sin servicio de audio
        }
    }

    // --- ACTIVAR ---

    void StartActivation()
    {
        ReadSettings();
        int id = ++generation;
        wasActive = false;
        heardNow = null;
        recent.Clear();
        SetState(State.Preparing);
        status.Text = "Preparando...";
        var worker = new Thread(delegate() { ActivateSteps(id); });
        worker.IsBackground = true;
        worker.Start();
    }

    void ActivateSteps(int id)
    {
        try
        {
            if (!EnsureModels(id) || !EnsureVoice(id))
            {
                Aborted(id);
                return;
            }
            bool cable = EnsureCable(id);
            if (id != generation) return;
            if (cable && !MicrophoneAllowed())
            {
                Aborted(id);
                return;
            }
            FixAudio(cable);
            string theirVoice = settings.TheirVoice;
            string permission = "";
            if (theirVoice == "clonada")
            {
                if (Ask(ConsentText, false)) permission = " --permiso-clonar";
                else theirVoice = "parecida";
            }
            string languages = settings.TheirLangs;
            lock (runner)
            {
                if (id != generation) return;
                string arguments;
                if (cable)
                {
                    string real = null;
                    try
                    {
                        real = Defaults.UseCable(SavedMic);
                    }
                    catch (Exception)
                    {
                        // Windows no dejó: hay que elegir CABLE Output en la app
                    }
                    micSwitched = real != null;
                    Info(micSwitched ? "\"CABLE Output\" quedó como micrófono predeterminado de Windows (al detener se vuelve a poner el tuyo)."
                                     : "No se pudo poner \"CABLE Output\" como micrófono predeterminado: elegilo vos en la videollamada.");
                    arguments = "conversar --source-lang " + settings.Speak + " --target-lang " + settings.HeardIn +
                                " --listen-lang " + settings.ListenIn + " --their-langs " + languages +
                                " --their-voice " + theirVoice + permission + " --voice-engine " + settings.MyVoice;
                    if (!string.IsNullOrEmpty(real)) arguments += " --input-device \"" + real.Replace("\"", "") + "\"";
                }
                else
                {
                    arguments = "escuchar --listen-lang " + settings.ListenIn + " --their-langs " + languages +
                                " --their-voice " + theirVoice + permission;
                }
                conversing = cable;
                UI(delegate { SetState(State.Starting); });
                runner.Start(arguments, null, delegate(int code) { OnConversationExit(id, code); });
            }
        }
        catch (Exception exc)
        {
            Error("No se pudo activar: " + exc.Message);
            Aborted(id);
        }
    }

    // Se cortó antes de arrancar (cancelaste, o faltaba algo y dijiste que no).
    void Aborted(int id)
    {
        UI(delegate
        {
            if (id != generation) return;
            generation++;
            RestoreMicrophone();
            SetState(State.Idle);
            TestFailed("no llegó a arrancar");
        });
    }

    void OnConversationExit(int id, int code)
    {
        UI(delegate
        {
            if (id != generation) return;  // lo detuviste vos
            generation++;
            RestoreMicrophone();
            SetState(State.Idle);
            Error("clonavoz se detuvo (código " + code + ").");
            TestFailed("se cerró solo");
            if (test == null)
            {
                MessageBox.Show(this, "clonavoz se detuvo. Lo último que dijo:\n\n" + RecentLines() +
                                "\n\nProbá \"Probar micrófono\", o \"Más opciones\" para ver más.", "clonavoz",
                                MessageBoxButtons.OK, MessageBoxIcon.Warning);
            }
        });
    }

    // DETENER, CANCELAR o cerrar la ventana.
    void StopEverything(bool byButton)
    {
        lock (runner)
        {
            generation++;
            runner.Stop();
        }
        RestoreMicrophone();
        if (state != State.Idle && byButton) Info("Detenido.");
        if (!IsDisposed) SetState(State.Idle);
    }

    void RestoreMicrophone()
    {
        micSwitched = false;
        if (!File.Exists(SavedMic)) return;  // no había otro micrófono predeterminado
        try
        {
            if (Defaults.Restore(SavedMic)) Info("Se volvió a poner tu micrófono de siempre como predeterminado.");
            else Info("Tu micrófono de siempre no está conectado: se vuelve a intentar la próxima vez.");
        }
        catch (Exception)
        {
        }
    }

    // Los modelos (una vez, con internet). Si elegiste un idioma nuevo, baja lo que falta.
    bool EnsureModels(int id)
    {
        List<string> wanted = settings.Languages();
        List<string> have = DownloadedLanguages();
        var missing = new List<string>();
        foreach (string code in wanted)
        {
            if (have == null || !have.Contains(code)) missing.Add(code);
        }
        if (missing.Count == 0) return true;
        string token = null;
        bool accepted;
        if (test != null)
        {
            accepted = TestAnswer(false);
            TestOutput.Line("PREGUNTA: descargar los modelos (" + string.Join(" ", missing.ToArray()) + ") -> " +
                            (accepted ? "sí" : "no"));
        }
        else
        {
            string space = FreeSpaceWarning();
            accepted = (bool)Invoke((Func<bool>)delegate
            {
                return DownloadDialog.Ask(this, have == null, missing, space, out token);
            });
        }
        if (!accepted)
        {
            Info("Sin los modelos no se puede traducir: tocá ACTIVAR cuando tengas internet.");
            return false;
        }
        UI(delegate { status.Text = "Descargando los modelos (se puede usar la PC mientras tanto)..."; });
        int result = RunAndWait(id, "download-models --languages " + string.Join(" ", wanted.ToArray()), token);
        if (id != generation) return false;
        if (result != 0 || !File.Exists(ReadyFile))
        {
            Error("No se pudo completar la descarga. Revisá la conexión a internet y tocá ACTIVAR de nuevo " +
                  "(sigue desde donde quedó).");
            return false;
        }
        Info("Listo: modelos descargados. Desde ahora funciona sin internet.");
        return true;
    }

    // Si en el pendrive (o disco) no entran los modelos.
    string FreeSpaceWarning()
    {
        try
        {
            var drive = new DriveInfo(Path.GetPathRoot(Path.GetFullPath(app)));
            double free = drive.AvailableFreeSpace / 1073741824.0;
            if (free >= 3.0) return null;
            return string.Format("Ojo: quedan {0:0.0} GB libres y hacen falta unos 2.7 GB. Liberá lugar antes de seguir.", free);
        }
        catch (Exception)
        {
            return null;
        }
    }

    List<string> DownloadedLanguages()
    {
        try
        {
            if (!File.Exists(ReadyFile)) return null;
            string text = File.ReadAllText(ReadyFile);  // "Modelos descargados para: es, en, pt"
            int colon = text.IndexOf(':');
            var codes = new List<string>();
            foreach (string code in text.Substring(colon + 1).Split(new char[] { ',', ' ', '\r', '\n' }, StringSplitOptions.RemoveEmptyEntries))
            {
                codes.Add(code.Trim());
            }
            return codes;
        }
        catch (Exception)
        {
            return null;
        }
    }

    // Lo que se puede arreglar solo antes de arrancar: las causas típicas de
    // "no me escuchan" o "no escucho nada".
    void FixAudio(bool cable)
    {
        try
        {
            string output = Defaults.FixOutput();
            if (output != null)
            {
                Info("Tu salida de audio predeterminada era el cable virtual (así no escuchabas la PC, y la otra " +
                     "persona se escucharía a sí misma): ahora es \"" + output + "\".");
            }
            if (cable)
            {
                MakeAudible("\"CABLE Output\"", Defaults.FindCable(1));
                MakeAudible("\"CABLE Input\"", Defaults.FindCable(0));
            }
            FixMicrophone();
        }
        catch (Exception)
        {
            // sin servicio de audio: lo dirá clonavoz
        }
    }

    void FixMicrophone()
    {
        try
        {
            string mic = Defaults.RealMicrophone(SavedMic);
            if (mic != null) MakeAudible("Tu micrófono (" + Defaults.Name(mic) + ")", mic);
        }
        catch (Exception)
        {
        }
    }

    void MakeAudible(string what, string id)
    {
        string fixes = Defaults.MakeAudible(id);
        if (fixes != null) Info(what + " " + fixes + " en Windows: ya se arregló.");
    }

    // Windows puede no dejar que los programas usen el micrófono (Privacidad).
    bool MicrophoneAllowed()
    {
        if (!MicrophoneBlocked()) return true;
        if (Ask("Windows no deja que los programas usen el micrófono (Configuración > Privacidad > Micrófono): " +
                "así clonavoz no te escucharía.\n\n¿Abrir esa configuración? Activá \"Acceso al micrófono\" y " +
                "\"Permitir que las aplicaciones de escritorio accedan al micrófono\", y después tocá ACTIVAR de nuevo.",
                false))
        {
            OpenFile("ms-settings:privacy-microphone");
        }
        Info("Windows no deja usar el micrófono: permitilo en Configuración > Privacidad > Micrófono y tocá " +
             "ACTIVAR de nuevo.");
        return false;
    }

    static bool MicrophoneBlocked()
    {
        const string store = @"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone";
        return Denied(Registry.LocalMachine, store) || Denied(Registry.CurrentUser, store)
            || Denied(Registry.CurrentUser, store + @"\NonPackaged");
    }

    static bool Denied(RegistryKey root, string path)
    {
        try
        {
            using (RegistryKey key = root.OpenSubKey(path))
            {
                return key != null && "Deny".Equals(key.GetValue("Value") as string, StringComparison.OrdinalIgnoreCase);
            }
        }
        catch (Exception)
        {
            return false;
        }
    }

    bool EnsureVoice(int id)
    {
        if (File.Exists(VoiceFile)) return true;
        if (!Ask("Ahora grabamos tu voz: 15 segundos.\n\nCuando toques Sí, leé en voz alta el texto que aparece en " +
                 "la ventana, con tu tono de siempre y sin ruido de fondo. Queda en el pendrive (carpeta datos), no " +
                 "en esta PC.\n\n¿Empezamos?", false))
        {
            Info("Sin tu voz grabada no se puede clonar: tocá ACTIVAR cuando quieras grabarla.");
            return false;
        }
        return RecordVoice(id);
    }

    bool RecordVoice(int id)
    {
        if (!MicrophoneAllowed()) return false;
        FixMicrophone();
        UI(delegate
        {
            reading.Text = "Leé en voz alta, con tu tono de siempre:\n\n" + ReadingText(settings.Speak);
            reading.Visible = true;
            status.Text = "Grabando tu voz: 15 segundos...";
        });
        int result = RunAndWait(id, "enroll --seconds 15", null);
        UI(delegate { reading.Visible = false; });
        if (id != generation) return false;
        if (result != 0 || !File.Exists(VoiceFile))
        {
            Error("No se pudo grabar tu voz.");
            ShowProblem("No se pudo grabar tu voz. Lo último que dijo:", "Revisá que el micrófono esté conectado y " +
                        "no silenciado (botón \"Probar micrófono\") y volvé a intentar.");
            return false;
        }
        Info("Listo: tu voz quedó grabada.");
        return true;
    }

    static string ReadingText(string code)
    {
        if (code == "en")
            return "\"Hi, how are you? I'm trying a program that translates what I say and speaks it with my own " +
                   "voice. It's amazing to talk with people from other countries like this. Let's see how it " +
                   "sounds: one, two, three, four, five.\"";
        if (code == "pt")
            return "\"Oi, tudo bem? Estou testando um programa que traduz o que eu digo e fala com a minha própria " +
                   "voz. É incrível poder conversar assim com pessoas de outros países. Vamos ver como fica: um, " +
                   "dois, três, quatro, cinco.\"";
        if (code == "es")
            return "\"Hola, ¿cómo estás? Estoy probando un programa que traduce lo que digo y lo dice con mi propia " +
                   "voz. Me parece increíble poder hablar así con gente de otros países. Vamos a ver qué tal suena: " +
                   "uno, dos, tres, cuatro, cinco.\"";
        return "(Cualquier texto, o contá qué hiciste hoy: lo que importa es que se escuche bien tu voz.)";
    }

    bool EnsureCable(int id)
    {
        if (CableReady()) return true;
        if (askedCable)
        {
            Info("Sin VB-CABLE: te traduzco lo que te dicen, pero a vos te escuchan con tu voz de siempre.");
            return false;
        }
        askedCable = true;
        if (!Ask(CableQuestion, false))
        {
            Info("Sin VB-CABLE: te traduzco lo que te dicen, pero a vos te escuchan con tu voz de siempre. " +
                 "Para instalarlo después: abajo, \"VB-CABLE: falta en esta PC\".");
            return false;
        }
        InstallCable();
        if (id != generation) return false;
        if (CableReady()) return true;
        Info("VB-CABLE no quedó instalado (si recién lo instalaste, reiniciá la PC y volvé a tocar ACTIVAR). " +
             "Por ahora: solo te traduzco lo que te dicen.");
        return false;
    }

    // Abre el instalador oficial de VB-CABLE (tal cual viene en la carpeta
    // "vbcable") y espera a que lo cierres. Si Windows deja al cable como
    // parlante o micrófono predeterminado, vuelve a poner los de antes.
    void InstallCable()
    {
        string zip = Path.Combine(app, "vbcable", "VBCABLE_Driver_Pack45.zip");
        if (!File.Exists(zip))
        {
            Info("Esta copia de clonavoz no trae VB-CABLE: se abre su página oficial para descargarlo.");
            OpenFile("https://vb-audio.com/Cable/");
            return;
        }
        string[] before = Defaults.Snapshot();
        string folder = Path.Combine(Path.GetTempPath(), "clonavoz-vbcable");
        try
        {
            if (Directory.Exists(folder)) Directory.Delete(folder, true);
            ZipFile.ExtractToDirectory(zip, folder);
            Info("Se abre el instalador oficial de VB-CABLE: Windows pide permiso de administrador (Sí) y en el " +
                 "instalador tocá \"Install Driver\" (o \"Remove Driver\" para quitarlo).");
            string setup = Environment.Is64BitOperatingSystem ? "VBCABLE_Setup_x64.exe" : "VBCABLE_Setup.exe";
            var start = new ProcessStartInfo(Path.Combine(folder, setup));
            start.UseShellExecute = true;
            start.Verb = "runas";
            start.WorkingDirectory = folder;
            using (Process installer = Process.Start(start))
            {
                installer.WaitForExit();
            }
            Thread.Sleep(3000);
            int restored = Defaults.RestoreSnapshot(before);
            if ((restored & 1) != 0) Info("Se volvió a poner tu parlante/auricular de antes como predeterminado.");
            if ((restored & 2) != 0) Info("Se volvió a poner tu micrófono de antes como predeterminado.");
        }
        catch (Win32Exception)
        {
            Info("No se abrió el instalador: hace falta aceptar el permiso de administrador.");
        }
        catch (Exception exc)
        {
            Error("No se pudo abrir el instalador de VB-CABLE: " + exc.Message);
        }
        finally
        {
            try
            {
                Directory.Delete(folder, true);
            }
            catch (Exception)
            {
            }
        }
        UI(RefreshCable);
    }

    // --- Los otros botones (cuando está detenido) ---

    void RunTask(Action<int> task)
    {
        if (state != State.Idle) return;
        ReadSettings();
        int id = ++generation;
        SetState(State.Preparing);
        var worker = new Thread(delegate()
        {
            try
            {
                task(id);
            }
            catch (Exception exc)
            {
                Error(exc.Message);
            }
            UI(delegate
            {
                if (id != generation) return;
                generation++;
                SetState(State.Idle);
            });
        });
        worker.IsBackground = true;
        worker.Start();
    }

    void RecordAgain(int id)
    {
        if (!Ask("¿Grabar tu voz de nuevo? (15 segundos, reemplaza la anterior)", false)) return;
        RecordVoice(id);
    }

    void TestMicrophone(int id)
    {
        if (!MicrophoneAllowed()) return;
        FixMicrophone();
        UI(delegate { status.Text = "Probando: hablá durante 8 segundos, el medidor de abajo tiene que moverse con tu voz."; });
        RunAndWait(id, "test-audio" + (CableReady() ? "" : " --skip-output"), null);
    }

    void InstallCableAsked(int id)
    {
        if (Ask(CableQuestion.Replace("\n\n(Si elegís No, igual te traduzco lo que te dicen.)", ""), false)) InstallCable();
    }

    // Corre un comando de clonavoz y espera a que termine (o a que lo canceles).
    int RunAndWait(int id, string arguments, string token)
    {
        int result = -1;
        using (var finished = new ManualResetEvent(false))
        {
            lock (runner)
            {
                if (id != generation) return -1;
                runner.Start(arguments, token, delegate(int code)
                {
                    result = code;
                    try
                    {
                        finished.Set();
                    }
                    catch (ObjectDisposedException)
                    {
                    }
                });
            }
            finished.WaitOne();
        }
        return result;
    }

    // --- Lo que escribe clonavoz ---

    void OnLine(string text, bool fromErrors)
    {
        UI(delegate { HandleLine(text, fromErrors); });
    }

    void HandleLine(string text, bool fromErrors)
    {
        if (text.StartsWith("@@"))
        {
            meter.Text = text.Length > 3 ? text.Substring(3) : "";
            return;
        }
        if (text.Contains("%|"))  // barra de progreso de una descarga
        {
            meter.Text = "Descargando: " + text.Trim();
            return;
        }
        TestOutput.Line(text);
        if (text.Trim().Length == 0) return;
        recent.Add(text);
        if (recent.Count > 12) recent.RemoveAt(0);
        if (state == State.Starting && (text.StartsWith("Listo:") || text.StartsWith("Escuchando...")))
        {
            SetState(State.Active);
        }

        int close = text.IndexOf(") > ");
        if (text.StartsWith("  Te dicen (") && close > 0)
        {
            lastSpeaker = "ellos";
            Append("Te dicen (" + text.Substring(12, close - 12) + "): " + text.Substring(close + 4), Slate, fontSaid);
            return;
        }
        if (text.StartsWith("  Vos (") && close > 0)
        {
            lastSpeaker = "vos";
            Append("Vos: " + text.Substring(close + 4), Gray, fontMine);
            return;
        }
        int arrow = text.IndexOf(" > ");
        if (text.StartsWith("          ") && arrow > 0)
        {
            string said = text.Substring(arrow + 3);
            if (lastSpeaker == "ellos") Append("      " + said, Blue, fontForYou);
            else Append("      (" + text.Substring(0, arrow).Trim() + ") " + said, Green, fontMineTranslated);
            return;
        }
        if (text.StartsWith("[clonavoz] "))
        {
            string message = text.Substring(11);
            int at = message.IndexOf("desde ahora te escuchan en ");
            if (at >= 0)
            {
                heardNow = message.Substring(at + 27).TrimEnd('.');
                if (state == State.Active) status.Text = ActiveText();
            }
            Append(message, message.StartsWith("Aviso") ? Amber : Gray, fontInfo);
            return;
        }
        string lowered = text.ToLowerInvariant();
        if (lowered.StartsWith("error") || lowered.StartsWith("problema") || lowered.StartsWith("traceback") ||
            lowered.StartsWith("no se ") || lowered.Contains("error:"))
        {
            Append(text, Red, fontError);
        }
        else if (lowered.StartsWith("aviso") || lowered.StartsWith("ok:") || lowered.StartsWith("listo"))
        {
            Append(text, lowered.StartsWith("aviso") ? Amber : Green, fontSaid);
        }
        else
        {
            Append(text, Gray, fromErrors ? fontInfo : fontMine);
        }
    }

    string RecentLines()
    {
        return string.Join("\n", recent.ToArray());
    }

    void Info(string text)
    {
        TestOutput.Line("[ventana] " + text);
        UI(delegate { Append(text, Gray, fontInfo); });
    }

    void Error(string text)
    {
        TestOutput.Line("[ventana] ERROR: " + text);
        UI(delegate { Append(text, Red, fontError); });
    }

    // Un aviso con las últimas líneas de clonavoz en el medio (se arma en el hilo de la
    // ventana, después de las líneas que todavía faltaba mostrar).
    void ShowProblem(string before, string after)
    {
        if (test != null) return;
        UI(delegate
        {
            MessageBox.Show(this, before + "\n\n" + RecentLines() + "\n\n" + after, "clonavoz",
                            MessageBoxButtons.OK, MessageBoxIcon.Warning);
        });
    }

    void Append(string text, Color color, Font font)
    {
        if (log.TextLength > 200000)  // que no crezca sin límite en una llamada larga
        {
            log.ReadOnly = false;
            int cut = log.Text.IndexOf('\n', 100000);
            log.Select(0, cut > 0 ? cut + 1 : 100000);
            log.SelectedText = "";
            log.ReadOnly = true;
        }
        log.SelectionStart = log.TextLength;
        log.SelectionLength = 0;
        log.SelectionColor = color;
        log.SelectionFont = font;
        log.AppendText(text + "\n");
        log.SelectionStart = log.TextLength;
        log.ScrollToCaret();
    }

    // Una pregunta de sí o no (desde el hilo de ACTIVAR). En las pruebas contesta sola.
    bool Ask(string text, bool testAnswer)
    {
        if (test != null)
        {
            bool answer = TestAnswer(testAnswer);
            TestOutput.Line("PREGUNTA: " + text.Split('\n')[0] + " -> " + (answer ? "sí" : "no"));
            return answer;
        }
        try
        {
            return (bool)Invoke((Func<bool>)delegate
            {
                return MessageBox.Show(this, text, "clonavoz", MessageBoxButtons.YesNo, MessageBoxIcon.Question) == DialogResult.Yes;
            });
        }
        catch (Exception)
        {
            return false;  // se cerró la ventana
        }
    }

    // En las pruebas, CLONAVOZ_PRUEBA_SI=1 contesta que sí a todo (para recorrer la primera vez entera).
    static bool TestAnswer(bool normal)
    {
        return Environment.GetEnvironmentVariable("CLONAVOZ_PRUEBA_SI") == "1" || normal;
    }

    void UI(Action action)
    {
        try
        {
            if (IsDisposed) return;
            if (InvokeRequired) BeginInvoke(action);
            else action();
        }
        catch (InvalidOperationException)
        {
            // la ventana se está cerrando
        }
    }

    static void OpenFile(string path)
    {
        try
        {
            Process.Start(path);
        }
        catch (Exception)
        {
        }
    }

    // El menú de consola de siempre (Iniciar.bat), con todas las opciones.
    public static void OpenConsoleMenu(string app)
    {
        var start = new ProcessStartInfo("cmd.exe", "/d /c \"\"" + Path.Combine(app, "Iniciar.bat") + "\"\"");
        start.UseShellExecute = false;
        start.WorkingDirectory = app;
        Process.Start(start);  // abre su propia ventana de consola
    }

    // --- Al abrir ---

    protected override void OnShown(EventArgs e)
    {
        base.OnShown(e);
        RestoreMicrophone();  // si la última vez se cerró de golpe (ej. se apagó la PC) sin volver a ponerlo
        if (test == null && settings.AutoStart) StartActivation();
        if (test != null) Report();
        if (test == "--prueba")
        {
            Demo();
            CloseAfter(2000, 0);
        }
        if (test == "--prueba-activar")
        {
            CloseAfter(6 * 60 * 1000, 2);  // si en 6 minutos no arrancó, falló
            StartActivation();
        }
    }

    void Report()
    {
        TestOutput.Line("VENTANA: abierta (" + ClientSize.Width + "x" + ClientSize.Height + ")");
        TestOutput.Line("CARPETA: " + app);
        TestOutput.Line("VB-CABLE: " + (CableReady() ? "instalado" : "no está"));
        List<string> have = DownloadedLanguages();
        TestOutput.Line("MODELOS: " + (have == null ? "faltan" : string.Join(" ", have.ToArray())));
        TestOutput.Line("TU VOZ: " + (File.Exists(VoiceFile) ? "grabada" : "falta"));
        TestOutput.Line("IDIOMAS: hablás " + settings.Speak + ", te escuchan en " + settings.HeardIn +
                        ", escuchás en " + settings.ListenIn + ", te hablan en " + settings.TheirLangs);
        TestOutput.Line("MICRÓFONO PREDETERMINADO: " + (Defaults.Name(Defaults.Get(1)) ?? "ninguno"));
    }

    // Cómo se ven los subtítulos (--prueba): unas líneas como las que escribe clonavoz.
    void Demo()
    {
        foreach (string line in new string[]
        {
            "  Te dicen (inglés) > Hi! How are you doing today?",
            "          es > ¡Hola! ¿Cómo estás hoy?",
            "  Vos (es) > Muy bien, ¿y vos? Contame cómo te fue.",
            "          en > Very well, and you? Tell me how it went.",
            "[clonavoz] Te hablan en portugués: desde ahora te escuchan en portugués.",
            "  Te dicen (portugués) > Foi ótimo, obrigado!",
            "          es > ¡Fue genial, gracias!",
            "@@ Vos [######--] hablando | Te dicen [--------] - | te escuchan en portugués",
        })
        {
            HandleLine(line, false);
        }
        SaveCapture(CapturePath);
    }

    [DllImport("user32.dll")]
    static extern bool PrintWindow(IntPtr window, IntPtr hdc, uint flags);

    void SaveCapture(string path)
    {
        if (path == null) return;
        try
        {
            Refresh();
            using (var bitmap = new Bitmap(Width, Height))
            {
                bool printed = false;
                using (Graphics g = Graphics.FromImage(bitmap))
                {
                    try
                    {
                        IntPtr hdc = g.GetHdc();
                        try
                        {
                            printed = PrintWindow(Handle, hdc, 2);  // PW_RENDERFULLCONTENT: también el texto
                        }
                        finally
                        {
                            g.ReleaseHdc(hdc);
                        }
                    }
                    catch (Exception)
                    {
                        // fuera de Windows
                    }
                }
                if (!printed) DrawToBitmap(bitmap, new Rectangle(0, 0, Width, Height));
                bitmap.Save(path, System.Drawing.Imaging.ImageFormat.Png);
            }
            TestOutput.Line("CAPTURA: " + path);
        }
        catch (Exception exc)
        {
            TestOutput.Line("CAPTURA: no se pudo (" + exc.Message + ")");
        }
    }

    void StopAfterTest()
    {
        var timer = new System.Windows.Forms.Timer();
        timer.Interval = 8000;  // que traduzca un rato
        timer.Tick += delegate
        {
            timer.Stop();
            SaveCapture(CapturePath);
            TestOutput.Line("DETENER");
            StopEverything(true);
            TestOutput.Line("MICRÓFONO PREDETERMINADO: " + (Defaults.Name(Defaults.Get(1)) ?? "ninguno"));
            TestOutput.Line("MICRÓFONO ANOTADO PARA VOLVER: " + (File.Exists(SavedMic) ? "sigue (MAL)" : "no"));
            ExitCode = File.Exists(SavedMic) ? 1 : 0;
            Close();
        };
        timer.Start();
    }

    void TestFailed(string why)
    {
        if (test != "--prueba-activar" || wasActive) return;
        TestOutput.Line("FALLÓ: " + why);
        CloseAfter(500, 1);
    }

    void CloseAfter(int milliseconds, int code)
    {
        var timer = new System.Windows.Forms.Timer();
        timer.Interval = milliseconds;
        timer.Tick += delegate
        {
            timer.Stop();
            if (IsDisposed) return;
            ExitCode = code;
            if (code == 2) TestOutput.Line("FALLÓ: no arrancó a tiempo");
            Close();
        };
        timer.Start();
    }
}

// Antes de la primera descarga: cuánto es y, opcional, el token para la voz natural.
sealed class DownloadDialog : Form
{
    readonly TextBox token = new TextBox();

    public static bool Ask(IWin32Window owner, bool first, List<string> missing, string spaceWarning, out string hfToken)
    {
        using (var dialog = new DownloadDialog(first, missing, spaceWarning))
        {
            bool accepted = dialog.ShowDialog(owner) == DialogResult.OK;
            string value = dialog.token.Text.Trim();
            hfToken = accepted && value.Length > 0 ? value : null;
            return accepted;
        }
    }

    DownloadDialog(bool first, List<string> missing, string spaceWarning)
    {
        float scale = MainForm.DpiScale(this);
        Text = "clonavoz: descargar los modelos";
        Font = new Font("Segoe UI", 10f);
        BackColor = Color.White;
        FormBorderStyle = FormBorderStyle.FixedDialog;
        MaximizeBox = false;
        MinimizeBox = false;
        StartPosition = FormStartPosition.CenterParent;
        ClientSize = new Size((int)(600 * scale), (int)(430 * scale));

        var layout = new TableLayoutPanel();
        layout.Dock = DockStyle.Fill;
        layout.ColumnCount = 1;
        layout.Padding = new Padding((int)(16 * scale));
        int width = (int)(565 * scale);

        var intro = Wrapped(first
            ? "Primera vez: hay que descargar los modelos de reconocimiento, traducción y voz (unos 2.7 GB, con " +
              "internet, una sola vez). Quedan en el pendrive: después funciona sin internet en cualquier PC."
            : "Para los idiomas que elegiste hay que descargar lo que falta (" + string.Join(", ", missing.ToArray()) +
              "), con internet.", width);
        intro.Font = new Font("Segoe UI", 10.5f, FontStyle.Bold);
        layout.Controls.Add(intro);
        if (spaceWarning != null)
        {
            var space = Wrapped(spaceWarning, width);
            space.ForeColor = Color.FromArgb(185, 28, 28);
            layout.Controls.Add(space);
        }

        layout.Controls.Add(Wrapped(
            "Voz natural (opcional, la más parecida a vos): sus creadores (Kyutai) piden aceptar una condición, " +
            "clonar solo voces con permiso de su dueño. Si la querés: creá una cuenta gratis en Hugging Face, " +
            "aceptá las condiciones de kyutai/pocket-tts y creá un token de tipo \"Read\":", width));
        var links = new FlowLayoutPanel();
        links.AutoSize = true;
        links.Controls.Add(Link("1. Crear cuenta", "https://huggingface.co/join"));
        links.Controls.Add(Link("2. Aceptar las condiciones", "https://huggingface.co/kyutai/pocket-tts"));
        links.Controls.Add(Link("3. Crear el token", "https://huggingface.co/settings/tokens"));
        layout.Controls.Add(links);
        layout.Controls.Add(Wrapped("Pegá el token acá (se usa solo para esta descarga, no se guarda):", width));
        token.UseSystemPasswordChar = true;
        token.Width = width;
        layout.Controls.Add(token);
        var note = Wrapped("Sin token se usa la voz liviana: también es tu voz, un poco menos natural. " +
                           "La natural se puede bajar después (\"Más opciones\", opción 10).", width);
        note.ForeColor = Color.DimGray;
        layout.Controls.Add(note);

        var buttons = new FlowLayoutPanel();
        buttons.AutoSize = true;
        buttons.FlowDirection = FlowDirection.RightToLeft;
        buttons.Dock = DockStyle.Fill;
        var cancel = new Button();
        cancel.Text = "Cancelar";
        cancel.AutoSize = true;
        cancel.DialogResult = DialogResult.Cancel;
        var download = new Button();
        download.Text = "Descargar";
        download.AutoSize = true;
        download.DialogResult = DialogResult.OK;
        buttons.Controls.Add(cancel);
        buttons.Controls.Add(download);
        layout.Controls.Add(buttons);
        AcceptButton = download;
        CancelButton = cancel;
        Controls.Add(layout);
    }

    static Label Wrapped(string text, int width)
    {
        var label = new Label();
        label.Text = text;
        label.AutoSize = true;
        label.MaximumSize = new Size(width, 0);
        label.Margin = new Padding(0, 4, 0, 6);
        return label;
    }

    static LinkLabel Link(string text, string url)
    {
        var link = new LinkLabel();
        link.Text = text;
        link.AutoSize = true;
        link.LinkClicked += delegate
        {
            try
            {
                Process.Start(url);
            }
            catch (Exception)
            {
            }
        };
        return link;
    }
}
