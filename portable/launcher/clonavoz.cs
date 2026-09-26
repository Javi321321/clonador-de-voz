// clonavoz.exe: un solo archivo para llevar en el pendrive.
//
// Adentro trae la versión portable entera (Python y todo lo que usa) como un
// .zip pegado al final del .exe. La primera vez que lo abrís se instala en la
// carpeta "clonavoz", al lado del .exe (en el mismo pendrive: nada se instala
// en la computadora), y después abre el menú. Las veces siguientes abre el
// menú directamente. Si es una versión nueva, actualiza el programa y
// conserva tu voz y los modelos descargados (carpeta "datos").
//
// Se compila con el compilador de C# que trae Windows (ver
// portable/build_windows.py). Opciones: --solo-instalar (instala y sale,
// para las pruebas), --carpeta <ruta> (dónde instalar).
using System;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Text;

static class Launcher
{
    const string Version = "__VERSION__";  // lo reemplaza build_windows.py

    static int Main(string[] args)
    {
        try
        {
            Console.OutputEncoding = new UTF8Encoding(false);  // sin BOM al principio
        }
        catch (IOException)
        {
            // sin consola (ej. redirigido): da igual
        }
        try
        {
            return Run(args);
        }
        catch (Exception exc)
        {
            Console.WriteLine();
            Console.WriteLine("ERROR: " + exc.Message);
            Pause();
            return 1;
        }
    }

    static int Run(string[] args)
    {
        string exe = Assembly.GetExecutingAssembly().Location;
        string here = Path.GetDirectoryName(Path.GetFullPath(exe));
        bool installOnly = false;
        string app = File.Exists(Path.Combine(here, "Iniciar.bat")) ? here : Path.Combine(here, "clonavoz");
        for (int i = 0; i < args.Length; i++)
        {
            if (args[i] == "--solo-instalar") installOnly = true;
            else if (args[i] == "--carpeta" && i + 1 < args.Length) app = Path.GetFullPath(args[++i]);
        }

        if (!IsInstalled(app))
        {
            if (!HasPayload(exe))
            {
                Console.WriteLine("No se encontró clonavoz en " + app + " y este .exe no trae el programa adentro.");
                Console.WriteLine("Descargá clonavoz.exe completo (el de ~1 GB) y ponelo en el pendrive.");
                Pause();
                return 1;
            }
            Install(exe, app);
        }
        if (installOnly) return 0;

        string menu = Path.Combine(app, "Iniciar.bat");
        var start = new ProcessStartInfo("cmd.exe", "/c \"\"" + menu + "\"\"");
        start.UseShellExecute = false;
        start.WorkingDirectory = app;
        using (Process process = Process.Start(start))
        {
            process.WaitForExit();
            return process.ExitCode;
        }
    }

    static bool IsInstalled(string app)
    {
        string version = Path.Combine(app, "version.txt");
        return File.Exists(Path.Combine(app, "Iniciar.bat"))
            && File.Exists(Path.Combine(app, "python", "python.exe"))
            && File.Exists(version)
            && File.ReadAllText(version).Trim() == Version;
    }

    static bool HasPayload(string exe)
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

    static void Install(string exe, string app)
    {
        using (ZipArchive zip = ZipFile.OpenRead(exe))
        {
            long total = 0;
            foreach (ZipArchiveEntry entry in zip.Entries) total += entry.Length;
            bool update = Directory.Exists(app);
            Console.WriteLine("==============================================================");
            Console.WriteLine(update ? "   Actualizando clonavoz (tu voz y los modelos se conservan)"
                                     : "   Instalando clonavoz (una sola vez)");
            Console.WriteLine("==============================================================");
            Console.WriteLine("Carpeta: " + app);
            Console.WriteLine("Todo queda en esa carpeta: en esta computadora no se instala nada.");
            CheckFreeSpace(app, total);

            Directory.CreateDirectory(app);
            string python = Path.Combine(app, "python");
            if (Directory.Exists(python))
            {
                Directory.Delete(python, true);  // el Python de la versión anterior
            }
            long done = 0;
            int shown = -1;
            string root = Path.GetFullPath(app) + Path.DirectorySeparatorChar;
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
                    Console.Write("\rCopiando el programa al pendrive... " + percent + "%   ");
                }
            }
            File.WriteAllText(Path.Combine(app, "version.txt"), Version + Environment.NewLine);
            Console.WriteLine();
            Console.WriteLine("Listo.");
            Console.WriteLine();
        }
    }

    static void CheckFreeSpace(string app, long needed)
    {
        try
        {
            var drive = new DriveInfo(Path.GetPathRoot(Path.GetFullPath(app)));
            long models = 3L * 1024 * 1024 * 1024;  // los modelos que se bajan después (~2.5 GB)
            if (drive.AvailableFreeSpace < needed + models)
            {
                Console.WriteLine(string.Format(
                    "AVISO: quedan {0:0.0} GB libres y hacen falta unos {1:0.0} GB (el programa y los modelos).",
                    drive.AvailableFreeSpace / 1073741824.0, (needed + models) / 1073741824.0));
            }
        }
        catch (Exception)
        {
            // si no se puede saber, se intenta igual
        }
    }

    static void Pause()
    {
        Console.WriteLine("Presioná Enter para cerrar.");
        try
        {
            Console.ReadLine();
        }
        catch (IOException)
        {
        }
    }
}
