"""First-run desktop shortcut (R3 §3) — Wird-style, done in Python.

On the first start of a dev checkout or portable package, drop
``Safa.lnk`` on the user's Desktop pointing at ``Safa.exe`` (portable)
or ``start.bat`` (dev).

Mechanism: a fixed PowerShell ``-Command`` script resolves the Desktop
(``GetFolderPath`` — OneDrive redirects just work) and creates the
link through the shell's own ``IShellLinkW``/``IPersistFile`` COM
interfaces, declared inline via ``Add-Type``. Unlike WScript.Shell —
which writes ANSI string fields and cannot even save into directories
with non-ANSI names — ``IShellLinkW`` keeps the Arabic description and
saves under Arabic user profiles. Target, working dir, icon and
description travel through environment variables, never interpolated
into the script text — paths may contain Arabic, spaces, quotes and
``$``.

Skip rules: not Windows · ``SAFA_NO_SHORTCUT`` · ``SOUND_SYSTEM_DATA_DIR``
(Docker, tests) · installed mode (installed.json — Inno owns shortcuts)
· marker ``<data>/.desktop-shortcut`` already present. The marker is
written after a successful create AND when the link already exists, so
a user who deletes the shortcut is never asked again. Failures write no
marker and log one non-fatal line — the app must start regardless.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

MARKER_NAME = ".desktop-shortcut"
TIMEOUT_S = 15

# Fixed -Command text. Exit codes: 0 created · 10 already existed ·
# 11 no Desktop path · 12 creation failed. Reads SAFA_LNK_TARGET,
# SAFA_LNK_WORKDIR, SAFA_LNK_ICON, SAFA_LNK_DESC and (tests only)
# SAFA_SHORTCUT_DIR from the environment — no path is ever
# interpolated into the script.
_PS_SCRIPT = """\
$desktop = $env:SAFA_SHORTCUT_DIR
if (-not $desktop) { $desktop = [Environment]::GetFolderPath('Desktop') }
if (-not $desktop) { exit 11 }
$desktop = [IO.Path]::GetFullPath($desktop)
$link = Join-Path $desktop 'Safa.lnk'
if (Test-Path -LiteralPath $link) { exit 10 }
$src = @'
using System;
using System.Runtime.InteropServices;
using System.Text;

[ComImport, Guid("00021401-0000-0000-C000-000000000046")]
public class CShellLink {}

[ComImport, InterfaceType(ComInterfaceType.InterfaceIsIUnknown),
 Guid("000214F9-0000-0000-C000-000000000046")]
public interface IShellLinkW {
    void GetPath([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder f,
                 int cch, IntPtr pfd, int flags);
    void GetIDList(out IntPtr ppidl);
    void SetIDList(IntPtr pidl);
    void GetDescription([Out, MarshalAs(UnmanagedType.LPWStr)]
                        StringBuilder n, int cch);
    void SetDescription([MarshalAs(UnmanagedType.LPWStr)] string n);
    void GetWorkingDirectory([Out, MarshalAs(UnmanagedType.LPWStr)]
                             StringBuilder d, int cch);
    void SetWorkingDirectory([MarshalAs(UnmanagedType.LPWStr)] string d);
    void GetArguments([Out, MarshalAs(UnmanagedType.LPWStr)]
                      StringBuilder a, int cch);
    void SetArguments([MarshalAs(UnmanagedType.LPWStr)] string a);
    void GetHotkey(out short h);
    void SetHotkey(short h);
    void GetShowCmd(out int c);
    void SetShowCmd(int c);
    void GetIconLocation([Out, MarshalAs(UnmanagedType.LPWStr)]
                         StringBuilder p, int cch, out int i);
    void SetIconLocation([MarshalAs(UnmanagedType.LPWStr)] string p,
                         int i);
    void SetRelativePath([MarshalAs(UnmanagedType.LPWStr)] string p,
                         int r);
    void Resolve(IntPtr hwnd, int flags);
    void SetPath([MarshalAs(UnmanagedType.LPWStr)] string f);
}

[ComImport, InterfaceType(ComInterfaceType.InterfaceIsIUnknown),
 Guid("0000010B-0000-0000-C000-000000000046")]
public interface IPersistFile {
    void GetClassID(out Guid id);
    void IsDirty();
    void Load([MarshalAs(UnmanagedType.LPWStr)] string f, int m);
    void Save([MarshalAs(UnmanagedType.LPWStr)] string f, bool r);
    void SaveCompleted([MarshalAs(UnmanagedType.LPWStr)] string f);
    void GetCurFile([MarshalAs(UnmanagedType.LPWStr)] out string f);
}

public static class SafaLnk {
    public static void Create(string path, string target, string workdir,
                              string icon, string desc) {
        IShellLinkW sl = (IShellLinkW)new CShellLink();
        sl.SetPath(target);
        sl.SetWorkingDirectory(workdir);
        sl.SetIconLocation(icon, 0);
        sl.SetDescription(desc);
        ((IPersistFile)sl).Save(path, true);
    }
}
'@
try {
    Add-Type -TypeDefinition $src -ErrorAction Stop
    [SafaLnk]::Create($link, $env:SAFA_LNK_TARGET, $env:SAFA_LNK_WORKDIR,
                      $env:SAFA_LNK_ICON, $env:SAFA_LNK_DESC)
    exit 0
} catch {
    exit 12
}
"""


# skip rules + delivery ---------------------------------------------------


def _installed_mode(root: Path) -> bool:
    """installed.json present and not overridden by portable.txt —
    mirrors backend.core.paths mode resolution."""
    return (root / "installed.json").is_file() and not (
        root / "portable.txt").is_file()


def _target_and_icon(root: Path) -> tuple[Path, Path]:
    exe = root / "Safa.exe"
    target = exe if exe.is_file() else root / "start.bat"
    launcher_ico = root / "packaging" / "windows" / "launcher" / "safa.ico"
    dist_ico = root / "frontend" / "dist" / "favicon.ico"
    if exe.is_file():
        icon = exe
    elif launcher_ico.is_file():
        icon = launcher_ico
    else:
        icon = dist_ico
    return target, icon


def _warn(msg: str) -> None:
    print(f"Warning: could not create the desktop shortcut ({msg}) — "
          "the app still runs.", flush=True)


def maybe_create_desktop_shortcut(
    root: Path,
    data_dir: Path | None = None,
    *,
    _run=None,
) -> str:
    """Best-effort Safa.lnk on the Desktop. Returns ``created`` ·
    ``exists`` · ``skipped`` · ``failed`` — never raises, never blocks
    startup for more than ~TIMEOUT_S once."""
    try:
        if sys.platform != "win32":
            return "skipped"
        env = os.environ
        if env.get("SAFA_NO_SHORTCUT", "").strip():
            return "skipped"
        if env.get("SOUND_SYSTEM_DATA_DIR", "").strip():
            return "skipped"
        if _installed_mode(root):
            return "skipped"
        marker = (data_dir or (root / "data")) / MARKER_NAME
        if marker.is_file():
            return "skipped"
        target, icon = _target_and_icon(root)
        child_env = dict(env)
        child_env["SAFA_LNK_TARGET"] = str(target)
        child_env["SAFA_LNK_WORKDIR"] = str(root)
        child_env["SAFA_LNK_ICON"] = str(icon)
        child_env["SAFA_LNK_DESC"] = "Safa (صفا)"
        kw = {}
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            kw["creationflags"] = subprocess.CREATE_NO_WINDOW
        rc = (_run or subprocess.run)(
            [
                "powershell", "-NoProfile", "-NonInteractive",
                "-ExecutionPolicy", "Bypass", "-Command", _PS_SCRIPT,
            ],
            env=child_env, timeout=TIMEOUT_S, capture_output=True, **kw,
        ).returncode
        if rc in (0, 10):
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.touch()
            return "created" if rc == 0 else "exists"
        _warn(f"powershell exit {rc}")
        return "failed"
    except Exception as e:
        _warn(f"{type(e).__name__}: {e}")
        return "failed"
