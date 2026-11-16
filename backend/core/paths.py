"""Repository root and well-known directories."""
import os
from pathlib import Path

ROOT: Path = Path(__file__).resolve().parents[2]
TOOLS_DIR = ROOT / "tools"
# ``SOUND_SYSTEM_MODELS_DIR`` relocates the model cache root (M10b: the
# package smoke test points a bundled copy at an existing models/ tree so
# nothing re-downloads). Must be set before this module is imported.
_models_env = os.environ.get("SOUND_SYSTEM_MODELS_DIR", "").strip()
MODELS_DIR = Path(_models_env) if _models_env else ROOT / "models"
DATA_DIR = ROOT / "data"
FIXTURES_DIR = ROOT / "tests" / "fixtures" / "generated"


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
