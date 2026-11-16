"""M10b: end-to-end smoke of every built portable package (slow).

For each ``dist_pkg/SoundSystem-*-win64-<variant>`` build we extract the
.zip into ``data/tmp/pkg smoke/تجربة صوت`` (Arabic + spaces, like a
friend's Downloads folder), start ``start.bat`` with a PATH stripped of
uv/node/python, verify /api/health + capabilities, run a short Podcast
job and fetch its output. ``SOUND_SYSTEM_MODELS_DIR`` points the packaged
app at the repo's models/ so nothing re-downloads.

Skipped entirely when dist_pkg/ has no built packages — build one with
``scripts/package.ps1 -Variant cpu|gpu`` first.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import smoke_package  # noqa: E402

pytestmark = pytest.mark.slow

DIST = ROOT / "dist_pkg"
MODELS = ROOT / "models"


def _packages() -> list[Path]:
    """Built package dirs in dist_pkg (non-full variants)."""
    if not DIST.is_dir():
        return []
    return sorted(
        p
        for p in DIST.glob("SoundSystem-*-win64-*")
        if p.is_dir()
        and "-full" not in p.name
        and (p / "start.bat").is_file()
    )


_PKGS = _packages()


def _source(pkg: Path) -> Path:
    """Prefer the shipped .zip (what a friend actually gets)."""
    z = pkg.parent / (pkg.name + ".zip")
    return z if z.is_file() else pkg


@pytest.mark.skipif(
    not _PKGS, reason="dist_pkg has no built packages (run scripts/package.ps1)"
)
@pytest.mark.parametrize("pkg", _PKGS, ids=lambda p: p.name)
def test_built_package_smoke(pkg: Path, tmp_path):
    src = _source(pkg)
    pkg_root = smoke_package.package_dir(src)
    res = smoke_package.smoke(
        pkg_root,
        port=8795,
        models_dir=MODELS,
        device="auto",
    )
    assert res["ok"], res
    h = res["health"]
    assert h["modules_ok"]
    if res["variant"] == "cpu":
        assert h["cuda_dlls"] is False
        assert h["device"] == "cpu"
    else:
        assert h["cuda_dlls"] is True
    assert res["output"]["bytes"] > 10_000
