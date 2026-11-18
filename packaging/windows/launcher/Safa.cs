// Safa.exe — tiny Windows launcher (M10c).
//
//   Safa.exe [--port N] [--no-browser] [--check-only] [models ...]
//
// Runs  <app>\python\python.exe -m backend.launcher <args>  (or the dev
// .venv python) inside a Job Object with KILL_ON_JOB_CLOSE so closing
// this window/process tree can never orphan uvicorn/worker/ffmpeg.
// All launcher logic (port check, single instance, health wait, browser,
// bilingual status) lives in backend/launcher.py — this file is only the
// process supervisor. Compiled with .NET Framework csc.exe (no NuGet).
using System;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

internal static class SafaLauncher
{
    // ---- Win32 ------------------------------------------------------------
    private const uint CREATE_SUSPENDED = 0x00000004;
    private const uint CREATE_UNICODE_ENVIRONMENT = 0x00000400;
    private const int JobObjectExtendedLimitInformation = 9;
    private const uint JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000;

    [StructLayout(LayoutKind.Sequential)]
    private struct SECURITY_ATTRIBUTES
    {
        public int nLength;
        public IntPtr lpSecurityDescriptor;
        public bool bInheritHandle;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct STARTUPINFO
    {
        public int cb;
        public string lpReserved, lpDesktop, lpTitle;
        public int dwX, dwY, dwXSize, dwYSize;
        public int dwXCountChars, dwYCountChars, dwFillAttribute, dwFlags;
        public short wShowWindow, cbReserved2;
        public IntPtr lpReserved2, hStdInput, hStdOutput, hStdError;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct PROCESS_INFORMATION
    {
        public IntPtr hProcess, hThread;
        public int dwProcessId, dwThreadId;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct JOBOBJECT_BASIC_LIMIT_INFORMATION
    {
        public long PerProcessUserTimeLimit, PerJobUserTimeLimit;
        public uint LimitFlags;
        // SIZE_T / ULONG_PTR fields are pointer-sized — uint breaks
        // x64 with ERROR_BAD_LENGTH on SetInformationJobObject.
        public UIntPtr MinimumWorkingSetSize, MaximumWorkingSetSize;
        public uint ActiveProcessLimit;
        public UIntPtr Affinity;
        public uint PriorityClass, SchedulingClass;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct IO_COUNTERS
    {
        public ulong ReadOperationCount, WriteOperationCount, OtherOperationCount;
        public ulong ReadTransferCount, WriteTransferCount, OtherTransferCount;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct JOBOBJECT_EXTENDED_LIMIT_INFORMATION
    {
        public JOBOBJECT_BASIC_LIMIT_INFORMATION BasicLimitInformation;
        public IO_COUNTERS IoInfo;
        public IntPtr ProcessMemoryLimit, JobMemoryLimit,
            PeakProcessMemoryUsed, PeakJobMemoryUsed;
    }

    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    private static extern IntPtr CreateJobObject(
        IntPtr lpJobAttributes, string lpName);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool SetInformationJobObject(
        IntPtr hJob, int infoClass,
        ref JOBOBJECT_EXTENDED_LIMIT_INFORMATION info, int infoLength);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool AssignProcessToJobObject(
        IntPtr hJob, IntPtr hProcess);

    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    private static extern bool CreateProcess(
        string lpApplicationName, StringBuilder lpCommandLine,
        ref SECURITY_ATTRIBUTES procAttr, ref SECURITY_ATTRIBUTES threadAttr,
        bool bInheritHandles, uint flags, IntPtr env, string lpCurrentDirectory,
        ref STARTUPINFO si, out PROCESS_INFORMATION pi);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern uint ResumeThread(IntPtr hThread);

    [DllImport("kernel32.dll")]
    private static extern uint WaitForSingleObject(IntPtr h, uint ms);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool GetExitCodeProcess(IntPtr h, out uint code);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool CloseHandle(IntPtr h);

    // ---- helpers -----------------------------------------------------------

    [DllImport("kernel32.dll")]
    private static extern IntPtr GetConsoleWindow();

    [DllImport("kernel32.dll")]
    private static extern IntPtr GetStdHandle(int nStdHandle);

    [DllImport("kernel32.dll")]
    private static extern uint GetFileType(IntPtr hFile);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int MessageBoxW(
        IntPtr hWnd, string text, string caption, uint type);

    private const uint MB_ICONWARNING = 0x30;
    private const int STD_OUTPUT_HANDLE = -11;
    private const uint FILE_TYPE_DISK = 1;
    private const uint FILE_TYPE_PIPE = 3;
    private static readonly IntPtr INVALID_HANDLE_VALUE = new IntPtr(-1);

    // Built /target:winexe: started from a shortcut there is NO console —
    // Console.OutputEncoding throws IOException and Console.WriteLine goes
    // nowhere (v0.1.0 died on that first line, so the app never opened).
    private static bool HasConsole() { return GetConsoleWindow() != IntPtr.Zero; }

    private static bool StdoutRedirected()
    {
        IntPtr h = GetStdHandle(STD_OUTPUT_HANDLE);
        if (h == IntPtr.Zero || h == INVALID_HANDLE_VALUE) return false;
        uint t = GetFileType(h);
        return t == FILE_TYPE_DISK || t == FILE_TYPE_PIPE;
    }

    private static bool CanPrint() { return HasConsole() || StdoutRedirected(); }

    private static void Bilingual(string en, string ar)
    {
        bool arabic = System.Globalization.CultureInfo
            .CurrentUICulture.Name.StartsWith("ar");
        string first = arabic ? ar : en, second = arabic ? en : ar;
        if (CanPrint())
        {
            Console.WriteLine(first);
            Console.WriteLine(second);
        }
        else
        {
            MessageBoxW(IntPtr.Zero, first + "\r\n\r\n" + second,
                "Safa (صفا)", MB_ICONWARNING);
        }
    }

    private static string Quote(string s) { return "\"" + s + "\""; }

    private static void WarnUnsupervised(string why)
    {
        Bilingual(
            "Warning: process supervision unavailable (" + why + ") — child processes may outlive this window.",
            "تحذير: مراقبة العمليات غير متاحة (" + why + ") — قد تستمر العمليات الفرعية بعد إغلاق هذه النافذة.");
    }

    private static string FindPython(string root, out bool broken)
    {
        broken = false;
        foreach (var rel in new[] {
            @"python\python.exe",
            @".venv\Scripts\python.exe",
        })
        {
            var p = Path.Combine(root, rel);
            if (File.Exists(p)) return p;
        }
        broken = Directory.Exists(Path.Combine(root, "python"));
        return null;
    }

    private static int Run(string[] args)
    {
        if (HasConsole())
        {
            try { Console.OutputEncoding = Encoding.UTF8; }
            catch (IOException) { }  // redirected/odd console: keep default
        }
        else if (StdoutRedirected())
        {
            Console.SetOut(new StreamWriter(
                Console.OpenStandardOutput(), new UTF8Encoding(false))
            { AutoFlush = true });
        }
        var root = Path.GetDirectoryName(
            System.Reflection.Assembly.GetExecutingAssembly().Location)
            .TrimEnd('\\');

        bool broken;
        var python = FindPython(root, out broken);
        if (python == null)
        {
            Bilingual(
                broken
                    ? "This package is incomplete (python runtime missing). Extract again into a fresh folder."
                    : "No Python runtime found — install from source or use an installer (see README.md).",
                broken
                    ? "هذه الحزمة غير مكتملة (ملف بايثون مفقود). فكّ الضغط مرة أخرى في مجلد جديد."
                    : "لم يُعثر على بايثون — ثبّت من المصدر أو استخدم المثبّت (راجع README.md).");
            if (HasConsole())
            {
                Console.WriteLine();
                Console.WriteLine("Press Enter to exit — اضغط Enter للخروج");
                Console.ReadLine();
            }
            return 4;
        }

        var cmdline = new StringBuilder();
        cmdline.Append(Quote(python))
            .Append(" -m backend.launcher");
        foreach (var a in args)
            cmdline.Append(' ').Append(Quote(a));

        var job = CreateJobObject(IntPtr.Zero, null);
        if (job == IntPtr.Zero)
        {
            // Job Objects need nothing special — failing here is odd; fall
            // back to a plain process so the app still starts.
            WarnUnsupervised(
                "CreateJobObject: " + new Win32Exception(Marshal.GetLastWin32Error()).Message);
            return RunPlain(python, cmdline.ToString(), root);
        }

        var info = new JOBOBJECT_EXTENDED_LIMIT_INFORMATION();
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        if (!SetInformationJobObject(
                job, JobObjectExtendedLimitInformation,
                ref info, Marshal.SizeOf(info)))
        {
            var setErr = new Win32Exception(Marshal.GetLastWin32Error()).Message;
            CloseHandle(job);
            WarnUnsupervised("SetInformationJobObject: " + setErr);
            return RunPlain(python, cmdline.ToString(), root);
        }

        var sa = new SECURITY_ATTRIBUTES();
        sa.nLength = Marshal.SizeOf(typeof(SECURITY_ATTRIBUTES));
        var si = new STARTUPINFO();
        si.cb = Marshal.SizeOf(typeof(STARTUPINFO));
        PROCESS_INFORMATION pi;
        var env = IntPtr.Zero;
        if (!CreateProcess(
                null, cmdline, ref sa, ref sa,
                true, // inherit console handles → bilingual status shows
                CREATE_SUSPENDED | CREATE_UNICODE_ENVIRONMENT,
                env, root, ref si, out pi))
        {
            var err = new Win32Exception(Marshal.GetLastWin32Error()).Message;
            CloseHandle(job);
            Bilingual(
                "Failed to start Python: " + err,
                "فشل تشغيل بايثون: " + err);
            return 4;
        }

        if (!AssignProcessToJobObject(job, pi.hProcess))
        {
            // e.g. we are inside a Job that forbids nesting — the app still
            // runs, but closing this window can orphan the child, so warn.
            var err = new Win32Exception(Marshal.GetLastWin32Error()).Message;
            Bilingual(
                "Warning: process supervision unavailable (" + err + ") — child processes may outlive this window.",
                "تحذير: مراقبة العمليات غير متاحة (" + err + ") — قد تستمر العمليات الفرعية بعد إغلاق هذه النافذة.");
        }
        ResumeThread(pi.hThread);
        CloseHandle(pi.hThread);

        WaitForSingleObject(pi.hProcess, 0xFFFFFFFF); // INFINITE
        uint code;
        GetExitCodeProcess(pi.hProcess, out code);
        CloseHandle(pi.hProcess);
        CloseHandle(job); // closes → every child dies (KILL_ON_JOB_CLOSE)
        return unchecked((int)code);
    }

    private static int RunPlain(string python, string cmdline, string root)
    {
        var psi = new ProcessStartInfo("cmd.exe", "/c " + cmdline)
        {
            WorkingDirectory = root,
            UseShellExecute = false,
        };
        var p = Process.Start(psi);
        p.WaitForExit();
        return p.ExitCode;
    }

    private static int Main(string[] args) { return Run(args); }
}
