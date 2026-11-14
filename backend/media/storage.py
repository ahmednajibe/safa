"""Storage layout for uploads and job working dirs (M3a plan §Constraints).

``data/media/<media_id>/{original.<ext>, source.wav, peaks.json, playback.m4a}``
``data/jobs/<job_id>/{work/, output/}``
"""
from __future__ import annotations

import re
import shutil
import uuid
from pathlib import Path

from backend.core.settings import get_settings

ALLOWED_EXTS: set[str] = {
    "wav", "mp3", "flac", "m4a", "aac", "ogg", "opus", "wma",
    "mp4", "mkv", "mov", "webm", "avi",
}

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _data_dir(data_dir: Path | None) -> Path:
    return data_dir if data_dir is not None else get_settings().data_dir


def media_dir(media_id: str, data_dir: Path | None = None) -> Path:
    """Path of the media folder (not created here — the upload flow does)."""
    return _data_dir(data_dir) / "media" / media_id


def job_dir(job_id: str, data_dir: Path | None = None) -> Path:
    """Path of the job folder; creates it with ``work/`` and ``output/``."""
    d = _data_dir(data_dir) / "jobs" / job_id
    (d / "work").mkdir(parents=True, exist_ok=True)
    (d / "output").mkdir(parents=True, exist_ok=True)
    return d


def free_space_bytes(path: Path) -> int:
    """Free bytes on the volume containing ``path`` (nearest existing parent)."""
    p = Path(path)
    while not p.exists():
        if p.parent == p:
            break
        p = p.parent
    return shutil.disk_usage(p).free


def display_name(filename: str) -> str:
    """Human name for DB/UI: strip dirs and control chars, keep unicode."""
    name = str(filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = _CONTROL.sub("", name).strip()
    return name[:200] or "file"


def new_id() -> str:
    return uuid.uuid4().hex


def sanitize_stem(name: str) -> str:
    """Filename-safe stem for output files; keeps Arabic and spaces."""
    stem = display_name(name).rsplit(".", 1)[0]
    stem = re.sub(r'[<>:"/\\|?*]', "_", stem).rstrip(" .")
    return stem[:100] or "output"
