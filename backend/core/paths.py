"""Repository root and well-known directories.

Mode resolution (M10c §5), computed once at import — env vars must be set
before this module is imported:

1. ``SOUND_SYSTEM_DATA_DIR`` — always wins (installers, Docker, tests).
2. ``installed.json`` next to the app (written by the Inno installer with
   the user-chosen data folder) → installed mode: ``DATA_DIR`` = its
   ``data_dir``, ``MODELS_DIR`` = its ``models_dir`` or ``<data>/models``.
3. ``portable.txt`` marker (portable zips) or nothing (dev checkout) →
   ``DATA_DIR`` = ``ROOT/data``, ``MODELS_DIR`` = ``ROOT/models``.

``portable.txt`` beats ``installed.json`` — a portable zip must stay
self-contained even if it is extracted over an installed copy. A corrupt
or incomplete ``installed.json`` falls back to portable/dev behavior:
a broken marker must never break startup.
"""
import json
import os
from pathlib import Path

ROOT: Path = Path(__file__).resolve().parents[2]
TOOLS_DIR = ROOT / "tools"
FIXTURES_DIR = ROOT / "tests" / "fixtures" / "generated"

# ``portable.txt`` marks portable packages (self-contained data/ folder).
PORTABLE = (ROOT / "portable.txt").is_file()


def _load_installed() -> dict:
    """``<root>/installed.json`` — written by the installer (M10c §5).
    Anything unreadable falls back to portable/dev behavior."""
    try:
        data = json.loads((ROOT / "installed.json").read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


_INSTALLED = {} if PORTABLE else _load_installed()
"""``<root>/installed.json`` contents — ``{}`` in portable/dev mode."""


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


_data_env = _env("SOUND_SYSTEM_DATA_DIR")
if _data_env:
    DATA_DIR = Path(_data_env)
elif _INSTALLED.get("data_dir"):
    DATA_DIR = Path(_INSTALLED["data_dir"])
else:
    DATA_DIR = ROOT / "data"

# ``SOUND_SYSTEM_MODELS_DIR`` relocates only the model cache root (M10b:
# the package smoke test points a bundled copy at an existing models/ tree
# so nothing re-downloads). Otherwise models follow the selected data
# root: env → <data>/models, installed.json's optional ``models_dir`` → it,
# portable/dev → ROOT/models.
_models_env = _env("SOUND_SYSTEM_MODELS_DIR")
if _models_env:
    MODELS_DIR = Path(_models_env)
elif _data_env:
    MODELS_DIR = Path(_data_env) / "models"
elif _INSTALLED.get("data_dir"):
    _m = _INSTALLED.get("models_dir")
    MODELS_DIR = Path(_m) if _m else DATA_DIR / "models"
else:
    MODELS_DIR = ROOT / "models"


# ``.safa-data`` marks a folder as Safa-owned data: the Windows uninstaller
# offers to delete the data dir ONLY when this file exists (a user-picked
# Documents folder or drive root is never offered for deletion).
DATA_MARKER_NAME = ".safa-data"


def ensure_data_marker() -> Path | None:
    """Installed mode only: drop ``<data>/.safa-data`` (app id + version).

    The installer writes the marker at install time; the app re-creates
    it on startup so an upgrade/reinstall never loses it. Portable mode
    and ``SOUND_SYSTEM_DATA_DIR`` (Docker, tests) never mark — env-chosen
    dirs are not the installer's folder.

    A folder that already holds foreign files and has no ``app.db`` is
    never marked (R2): the uninstaller keys the data-deletion offer off
    the marker, so marking someone else's Documents would put their
    files one dialog away from deletion.
    """
    if PORTABLE or _data_env or not _INSTALLED.get("data_dir"):
        return None
    try:
        from backend.core.release_info import APP_ID, VERSION
        content = f"{APP_ID} {VERSION}\n"
    except Exception:
        content = "safa\n"
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        marker = DATA_DIR / DATA_MARKER_NAME
        if marker.is_file():
            return marker
        foreign_in_use = any(DATA_DIR.iterdir()) and not (
            DATA_DIR / "app.db"
        ).is_file()
        if foreign_in_use:
            return None
        marker.write_text(content, encoding="utf-8")
        return marker
    except OSError:
        return None


def tmp_dir(data_dir: Path | None = None) -> Path:
    """``<data>/tmp`` — the app-local temp/cache root (M10: keep C: clean)."""
    d = (data_dir or DATA_DIR) / "tmp"
    d.mkdir(parents=True, exist_ok=True)
    return d


def redirect_temp_env(data_dir: Path | None = None) -> Path:
    """Point ``TEMP``/``TMP``/``TMPDIR`` at ``<data>/tmp`` so tempfile and
    every child process write on the app drive, never the system drive.
    Idempotent; call before importing anything that might cache a tempdir."""
    d = tmp_dir(data_dir)
    for var in ("TEMP", "TMP", "TMPDIR"):
        os.environ[var] = str(d)
    return d
