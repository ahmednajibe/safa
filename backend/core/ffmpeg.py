"""Locate and query ffmpeg/ffprobe.

Resolution order (SPEC §4): ``tools/ffmpeg/bin`` first, then PATH.
All subprocess calls use argument lists (paths may contain Arabic/spaces).
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from backend.core.paths import TOOLS_DIR


class FfmpegNotFound(RuntimeError):
    """Neither tools/ffmpeg/bin nor PATH provides the requested binary."""


def _exe(name: str) -> str:
    return name + ".exe" if os.name == "nt" else name


def _ensure_executable(path: Path, is_nt: bool | None = None) -> None:
    """Restore the +x bit a bundled POSIX binary can lose (zip extracts
    strip modes; tars preserve them). No-op on Windows; never raises."""
    if is_nt is None:
        is_nt = os.name == "nt"
    if is_nt:
        return
    try:
        if not os.access(path, os.X_OK):
            path.chmod(0o755)
    except OSError:
        pass


def _find(binary: str) -> Path:
    local = TOOLS_DIR / "ffmpeg" / "bin" / _exe(binary)
    if local.exists():
        _ensure_executable(local)
        return local
    which = shutil.which(binary)
    if which:
        return Path(which)
    raise FfmpegNotFound(f"{binary} not found in {local.parent} or on PATH")


def find_ffmpeg() -> Path:
    return _find("ffmpeg")


def find_ffprobe() -> Path:
    return _find("ffprobe")


def ffmpeg_version() -> str:
    """First line of `ffmpeg -version`, e.g. 'ffmpeg version 7.1.1-...'."""
    out = subprocess.run(
        [str(find_ffmpeg()), "-version"],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.splitlines()[0].strip()


def has_encoder(name: str) -> bool:
    """True if `name` appears in `ffmpeg -hide_banner -encoders` output."""
    out = subprocess.run(
        [str(find_ffmpeg()), "-hide_banner", "-encoders"],
        capture_output=True,
        text=True,
        check=True,
    )
    for line in out.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == name:
            return True
    return False
