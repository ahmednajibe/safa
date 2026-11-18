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
import threading
import uuid
from pathlib import Path

from backend.core.paths import ROOT
from backend.media.storage import free_space_bytes

PICKER_TIMEOUT_S = 120
# Save As: people browse/rename longer than they pick a folder; the dialog
# is killed (= cancelled) after this.
SAVE_AS_TIMEOUT_S = 600

# Native dialogs run in a throwaway interpreter: DPI-aware *before* Tk()
# (Windows 11 scaling), withdrawn root so only the dialog shows, topmost +
# lift/focus so it can't hide behind the browser. The chosen path goes to
# stdout as UTF-8 (the parent decodes UTF-8 — without this an Arabic folder
# name was mangled through the ANSI code page); empty means cancelled.
_DIALOG_PRELUDE = """\
import sys
sys.stdout.reconfigure(encoding="utf-8")
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
    root.lift()
    root.focus_force()
except tk.TclError:
    pass
"""

_PICKER_SCRIPT = _DIALOG_PRELUDE + """\
try:
    _path = filedialog.askdirectory(parent=root)
finally:
    root.destroy()
sys.stdout.write(_path or "")
"""

# v0.1.1 server-side "Save as…": the browser's File System Access picker was
# cancelled by Chrome itself after the user pressed Save (AbortError, no
# file), so the server — which runs on the user's own PC — shows the native
# Save As window and copies the file. argv: <initial name> <.ext>.
_SAVE_AS_SCRIPT = _DIALOG_PRELUDE + """\
_name, _ext = sys.argv[1], sys.argv[2]
_types = [(_ext.lstrip(".").upper() + " file", "*" + _ext)] if _ext else []
try:
    _path = filedialog.asksaveasfilename(
        parent=root,
        initialfile=_name,
        defaultextension=_ext,
        filetypes=_types + [("All files", "*.*")],
        confirmoverwrite=True,
    )
finally:
    root.destroy()
sys.stdout.write(_path or "")
"""

# One native dialog at a time across the whole app (folder picker + Save as).
PICK_LOCK = threading.Lock()


class OutputDirInvalid(Exception):
    """The chosen/pasted folder fails validation → 422 output_dir_invalid."""


class PickerUnavailable(Exception):
    """The dialog process can't run on this machine → 503 picker_unavailable."""


def validate_output_dir(
    raw: str,
    project_root: Path | None = None,
    allow_drive_root: bool = False,
) -> Path:
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
    # normcase: on Windows resolve() returns the on-disk letter case, so a
    # dialog/typed path differing only in case (c:/users/... vs C:/Users/...)
    # must not count as a link escape
    if os.path.normcase(str(resolved)) != os.path.normcase(str(lexical)):
        raise OutputDirInvalid(f"link escape: {raw!r}")
    if resolved.parent == resolved and not allow_drive_root:
        raise OutputDirInvalid(f"drive root: {raw!r}")
    root_r = root.resolve()
    if resolved == root_r or root_r in resolved.parents:
        raise OutputDirInvalid(f"inside project: {raw!r}")
    # R2 #2: installed-mode DATA_DIR/MODELS_DIR live outside the repo —
    # exports auto-saved into them would nest next to app.db, the job
    # store and the model cache. Read them lazily so env/mode changes and
    # test patches apply.
    from backend.core import paths
    for guarded in (paths.DATA_DIR, paths.MODELS_DIR):
        g = Path(guarded).resolve()
        if resolved == g or g in resolved.parents:
            raise OutputDirInvalid(f"inside app data dir: {raw!r}")
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


def build_save_as_command(initial_name: str) -> list[str]:
    """Argv for the Save As subprocess (injected in tests)."""
    ext = Path(initial_name).suffix
    return [sys.executable, "-c", _SAVE_AS_SCRIPT, initial_name, ext]


def _run_dialog(cmd: list[str], timeout_s: float | None) -> str | None:
    timeout = PICKER_TIMEOUT_S if timeout_s is None else timeout_s
    try:
        cp = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
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


def run_picker(timeout_s: float | None = None) -> str | None:
    """Open the folder dialog; returns the picked path, ``None`` on cancel
    (including the 120 s timeout — nothing was chosen). Raises
    :class:`PickerUnavailable` when the dialog can't run at all."""
    return _run_dialog(build_picker_command(), timeout_s)


def run_save_as(initial_name: str, timeout_s: float | None = None) -> str | None:
    """Open the native Save As dialog; returns the chosen file path or
    ``None`` on cancel/timeout. Raises :class:`PickerUnavailable` when no
    GUI is available (Docker/headless) — callers fall back to a download."""
    return _run_dialog(
        build_save_as_command(initial_name),
        SAVE_AS_TIMEOUT_S if timeout_s is None else timeout_s,
    )


def validate_save_target(raw: str, ext: str) -> Path:
    """Vet a Save As target: its folder passes :func:`validate_output_dir`
    (drive roots allowed — the user explicitly chose the spot), the name
    keeps the output's real extension (Save As never converts), and an
    existing link/directory at the target is refused."""
    p = Path(str(raw).strip().strip('"'))
    if not p.is_absolute() or not p.name:
        raise OutputDirInvalid(f"not an absolute file path: {raw!r}")
    if ext and p.suffix.lower() != ext.lower():
        p = p.with_name(p.name + ext)
    folder = validate_output_dir(str(p.parent), allow_drive_root=True)
    target = folder / p.name
    if target.is_symlink() or target.is_dir():
        raise OutputDirInvalid(f"not a regular file target: {raw!r}")
    return target


def copy_to(src: Path, target: Path) -> Path:
    """Copy ``src`` to exactly ``target`` (the user confirmed any overwrite
    in the dialog): ``.<name>.<uuid>.tmp`` beside it, then ``os.replace`` —
    never a half-written file at ``target``."""
    size = Path(src).stat().st_size
    if free_space_bytes(target.parent) < size:
        raise OSError("not enough free space")
    tmp = target.parent / f".{target.name}.{uuid.uuid4().hex}.tmp"
    try:
        with Path(src).open("rb") as r, tmp.open("wb") as w:
            shutil.copyfileobj(r, w, 1024 * 1024)
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return target


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


def _claim_target(dst_dir: Path, name: str) -> Path:
    """Reserve ``name`` (or ``stem (2).ext``, ``(3)``…) by *creating* it
    with ``O_CREAT|O_EXCL`` — the check and the reservation are one
    atomic syscall, so two concurrent savers can never pick the same
    target (R2 #5: exists()-then-write raced and overwrote/failed).
    Returns the path of the empty placeholder the caller must fill."""
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    else:
        ext = dot + ext
    for i in range(1, 10_000):
        cand = dst_dir / (name if i == 1 else f"{stem} ({i}){ext}")
        try:
            fd = os.open(cand, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        except FileExistsError:
            continue
        os.close(fd)
        return cand
    raise OSError(f"no free name for {name!r}")


def _auto_copy(src: Path, dst_dir: Path) -> Path:
    """Copy ``src`` into ``dst_dir`` atomically under a non-colliding name
    (``O_EXCL`` placeholder claim + ``.<name>.<uuid>.tmp`` +
    ``os.replace``). Raises OSError on any failure — the caller turns it
    into ``auto_save_failed``."""
    if not dst_dir.is_dir():
        raise OSError(f"output folder missing: {dst_dir}")
    size = src.stat().st_size
    if free_space_bytes(dst_dir) < size:
        raise OSError("not enough free space")
    target = _claim_target(dst_dir, src.name)
    tmp = dst_dir / f".{src.name}.{uuid.uuid4().hex}.tmp"
    try:
        with src.open("rb") as r, tmp.open("wb") as w:
            shutil.copyfileobj(r, w, 1024 * 1024)
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        target.unlink(missing_ok=True)  # release the placeholder claim
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
