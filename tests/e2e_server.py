"""M9a E2E uvicorn app factory: same app as production but a temp data dir
(SS_E2E_DATA env) so live runs never touch data/."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.core.settings import Settings  # noqa: E402
from backend.main import create_app  # noqa: E402

_data = Path(os.environ["SS_E2E_DATA"])
app = create_app(
    Settings(data_dir=_data, db_path=_data / "app.db")
)
