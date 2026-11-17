"""Shared test helpers: generated fixtures on demand, real-recording skips."""
import os
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


@pytest.fixture(scope="session", autouse=True)
def _no_desktop_shortcut():
    """Belt-and-braces: no test may ever create a real Desktop shortcut
    (backend.core.shortcut honours SAFA_NO_SHORTCUT=1)."""
    prev = os.environ.get("SAFA_NO_SHORTCUT")
    os.environ["SAFA_NO_SHORTCUT"] = "1"
    yield
    if prev is None:
        os.environ.pop("SAFA_NO_SHORTCUT", None)
    else:
        os.environ["SAFA_NO_SHORTCUT"] = prev


@pytest.fixture(scope="session")
def real_dir():
    """User's real recordings (gitignored); skip when absent."""
    if not REAL_DIR.is_dir():
        pytest.skip("tests/fixtures/real missing")
    return REAL_DIR


# Modules that bind ``detect_capabilities`` by name at import time —
# patching only backend.core.capabilities would miss them.
_CAPS_IMPORTERS = (
    "backend.core.capabilities", "backend.api.jobs", "backend.api.operations",
    "backend.api.estimate", "backend.api.health", "backend.jobs.store",
    "backend.pipeline.runner", "backend.pipeline.estimates",
)


def all_models_caps():
    """Real capabilities of this machine, but with every model reported as
    downloaded — for tests of rules/API contracts, not of disk state (CI
    has no ~2.3 GB models tree)."""
    from dataclasses import replace

    from backend.core.capabilities import refresh_capabilities

    caps = refresh_capabilities()
    return replace(caps, models={k: True for k in caps.models})


def patch_models_present(mp):
    import importlib

    fake = all_models_caps()
    for name in _CAPS_IMPORTERS:
        mod = importlib.import_module(name)
        if hasattr(mod, "detect_capabilities"):
            mp.setattr(mod, "detect_capabilities", lambda: fake)
    return fake


@pytest.fixture
def models_present(monkeypatch):
    """Every model reported present, independent of the models/ tree."""
    return patch_models_present(monkeypatch)


skip_on_ci_machine_check = pytest.mark.skipif(
    bool(os.environ.get("CI")),
    reason="checks this machine's downloaded models; CI has none",
)
