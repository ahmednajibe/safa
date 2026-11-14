"""Repository root and well-known directories."""
from pathlib import Path

ROOT: Path = Path(__file__).resolve().parents[2]
TOOLS_DIR = ROOT / "tools"
MODELS_DIR = ROOT / "models"
DATA_DIR = ROOT / "data"
FIXTURES_DIR = ROOT / "tests" / "fixtures" / "generated"
