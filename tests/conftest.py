"""Shared test helpers: generated fixtures on demand, real-recording skips."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from backend.core.paths import FIXTURES_DIR  # noqa: E402

REAL_DIR = ROOT / "tests" / "fixtures" / "real"


@pytest.fixture(scope="session", autouse=True)
def generated_fixtures():
    """Ensure the M1 generated fixtures exist (cheap no-op when present)."""
    if (
        not (FIXTURES_DIR / "fixtures.json").exists()
        or not (FIXTURES_DIR / "sep_mix.wav").exists()
    ):
        import make_fixtures

        make_fixtures.main()
    return FIXTURES_DIR


@pytest.fixture(scope="session")
def real_dir():
    """User's real recordings (gitignored); skip when absent."""
    if not REAL_DIR.is_dir():
        pytest.skip("tests/fixtures/real missing")
    return REAL_DIR
