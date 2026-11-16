"""SPEC §12 "Always save to" folder: validation, the native folder picker,
the atomic auto-copy and the File-Explorer reveal.

The folder picker runs in a short-lived *subprocess* (tkinter ``askdirectory``
needs its own window loop; keeping it out of the API process also keeps a hung
dialog from wedging a worker thread — single-flight + 120 s timeout handle
that). ``build_picker_command()`` is the seam tests use to inject a fake
command; nothing here opens a real dialog during tests.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

from backend.core.paths import ROOT
from backend.media.storage import free_space_bytes

PICKER_TIMEOUT_S = 120

# askdirectory in a throwaway interpreter: DPI-aware *before* Tk() (Windows 11
# scaling), withdrawn root so only the dialog shows, topmost so it can't hide
# behind the browser. The chosen path goes to stdout; empty means cancelled.
_PICKER_SCRIPT = """\
import sys
try:
    import ctypes
    ctypes.windll.user32.SetProcessDpiAwarenessContext(-4)
except Exception:
    pass
import tkinter as tk
from tkinter import filedialog
root = tk.Tk()
root.withdraw()
try:
    root.wm_attributes("-topmost", 1)
except tk.TclError:
    pass
try:
    _path = filedialog.askdirectory(parent=root)
finally:
    root.destroy()
sys.stdout.write(_path or "")
"""


class OutputDirInvalid(Exception):
    """The chosen/pasted folder fails validation → 422 output_dir_invalid."""


class PickerUnavailable(Exception):
    """The dialog process can't run on this machine → 503 picker_unavailable."""


def validate_output_dir(raw: str, project_root: Path | None = None) -> Path:
    """Resolve + vet an output folder (SPEC §12).

    Rejects: empty/missing paths, non-directories, drive roots, anything
    inside the repo (auto-saving into ``F:\\sound_system`` could nest
    outputs under ``data/jobs``), symlink/junction escapes (``resolve()``
    changing the path), and folders we can't write to. Returns the resolved
    path on success.
    """
    root = Path(project_root) if project_root is not None else Path(ROOT)
    if not raw or not str(raw).strip().strip('"'):
        raise OutputDirInvalid("empty path")
    cleaned = os.path.expanduser(str(raw).strip().strip('"').strip())
    p = Path(cleaned)
    if not p.is_absolute():
        p = Path.cwd() / p
    lexical = Path(os.path.normpath(str(p)))
    resolved = lexical.resolve()
    if resolved != lexical:
        raise OutputDirInvalid(f"link escape: {raw!r}")
    if resolved.parent == resolved:
        raise OutputDirInvalid(f"drive root: {raw!r}")
    root_r = root.resolve()
    if resolved == root_r or root_r in resolved.parents:
        raise OutputDirInvalid(f"inside project: {raw!r}")
    if not resolved.is_dir():
        raise OutputDirInvalid(f"not a directory: {raw!r}")
    # Single create+delete probe for writability. NOT tempfile.mkstemp —
    # on Windows it retries PermissionError ~forever (os.access ignores
    # ACL denies), so a denied folder would hang instead of failing.
    probe = resolved / f".ss_probe_{uuid.uuid4().hex}"
    try:
        fd = os.open(probe, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        os.close(fd)
        probe.unlink()
    except OSError as exc:
        raise OutputDirInvalid(f"not writable: {raw!r}") from exc
    return resolved


def build_picker_command() -> list[str]:
    """Argv for the folder-picker subprocess (injected in tests)."""
    return [sys.executable, "-c", _PICKER_SCRIPT]


def run_picker(timeout_s: float | None = None) -> str | None:
    """Open the folder dialog; returns the picked path, ``None`` on cancel
    (including the 120 s timeout — nothing was chosen). Raises
    :class:`PickerUnavailable` when the dialog can't run at all."""
    timeout = PICKER_TIMEOUT_S if timeout_s is None else timeout_s
    try:
        cp = subprocess.run(
            build_picker_command(),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return None
    except OSError as exc:
        raise PickerUnavailable(str(exc)) from exc
    if cp.returncode != 0:
        raise PickerUnavailable(
            (cp.stderr or f"exit {cp.returncode}")[:300]
        )
    return cp.stdout.strip() or None


def reveal_in_explorer(path: Path, sys_platform: str | None = None) -> None:
    """Reveal ``path`` in the OS file manager (arg lists only — paths may
    contain Arabic/spaces; odd opener exit codes ignored by not waiting).
    Windows selects the file in Explorer; macOS reveals it in Finder;
    Linux opens the containing folder (``xdg-open`` can't select a file)."""
    plat = sys_platform if sys_platform is not None else sys.platform
    p = Path(path)
    if plat == "win32":
        subprocess.Popen(["explorer.exe", f'/select,"{p}"'])
    elif plat == "darwin":
        subprocess.Popen(["open", "-R", str(p)])
    else:
        target = p if p.is_dir() else p.parent
        subprocess.Popen(["xdg-open", str(target)])


def _non_colliding(dst_dir: Path, name: str) -> Path:
    """First free target name: ``name``, then ``stem (2).ext``, ``(3)``…"""
    cand = dst_dir / name
    if not cand.exists():
        return cand
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    else:
        ext = dot + ext
    for i in range(2, 10_000):
        cand = dst_dir / f"{stem} ({i}){ext}"
        if not cand.exists():
            return cand
    raise OSError(f"no free name for {name!r}")


def _auto_copy(src: Path, dst_dir: Path) -> Path:
    """Copy ``src`` into ``dst_dir`` atomically under a non-colliding name
    (``.<name>.<uuid>.tmp`` + ``os.replace``). Raises OSError on any
    failure — the caller turns it into ``auto_save_failed``."""
    if not dst_dir.is_dir():
        raise OSError(f"output folder missing: {dst_dir}")
    size = src.stat().st_size
    if free_space_bytes(dst_dir) < size:
        raise OSError("not enough free space")
    target = _non_colliding(dst_dir, src.name)
    tmp = dst_dir / f".{src.name}.{uuid.uuid4().hex}.tmp"
    try:
        with src.open("rb") as r, tmp.open("wb") as w:
            shutil.copyfileobj(r, w, 1024 * 1024)
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return target


def auto_save_outputs(store, job_id: str, kind: str, outputs) -> None:
    """Copy every job output into the configured folder (SPEC §12).

    Never previews, never configured → no-op. Every failure becomes one
    ``auto_save_failed{path}`` job warning — the job always completes; a
    full success is recorded as ``saved_to{path}`` and per-file
    ``job_files.saved_path`` for the reveal endpoint.
    """
    if kind == "preview":
        return
    raw = store.get_setting("output_dir")
    if not raw:
        return
    dst = Path(raw)
    saved = 0
    for f in outputs:
        try:
            target = _auto_copy(Path(f.path), dst)
        except Exception:
            break  # remaining files don't matter once the copy failed
        store.set_saved_path(job_id, f.name, target)
        saved += 1
    if saved == len(outputs):
        if outputs:
            store.add_warning(job_id, "saved_to", {"path": str(dst)})
    else:
        store.add_warning(job_id, "auto_save_failed", {"path": str(dst)})
