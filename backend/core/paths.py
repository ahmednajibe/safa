"""Repository root and well-known directories."""
import os
from pathlib import Path

ROOT: Path = Path(__file__).resolve().parents[2]
TOOLS_DIR = ROOT / "tools"
MODELS_DIR = ROOT / "models"
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
