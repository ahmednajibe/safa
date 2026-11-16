"""M10c §7: release-info.toml is the single source of truth for release
names/URLs. These tests pin the contract every pipeline consumer relies
on (installer scripts, manifests, docs, tests)."""
from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.core import release_info as ri  # noqa: E402
from backend.core.models_manifest import MODELS  # noqa: E402


def test_version_comes_from_pyproject():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))
    assert ri.VERSION == pyproject["project"]["version"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", ri.VERSION)


def test_repo_url_is_single_placeholder():
    assert ri.REPO_URL == "https://github.com/<owner>/safa"


def test_every_asset_renders_with_version():
    names = ri.asset_names("9.9.9")
    for key, name in names.items():
        if "{" in name:  # per-volume pattern keeps {n}/{nnn}
            rendered = ri.asset(key, ver="9.9.9", n=1, nnn="001")
            assert "{" not in rendered, key
        else:
            rendered = name
        if "{ver}" in ri.ASSETS[key]:
            assert "9.9.9" in rendered, f"{key} missing version: {rendered}"


def test_windows_asset_names_are_safa_branded():
    assert ri.asset("online_setup") == f"Safa-{ri.VERSION}-win-x64-setup.exe"
    assert ri.asset("cpu_portable_zip").endswith("-cpu-portable.zip")
    assert "safa" in ri.asset("macos_tarball").lower()


def test_gpu_slice_pattern_renders_n():
    assert ri.asset("gpu_offline_slice", n=1).endswith("setup-1.bin")
    assert ri.asset("gpu_portable_7z", nnn="002").endswith(".7z.002")


def test_model_packs_reference_real_models():
    for pack, keys in ri.MODEL_PACKS.items():
        assert keys, f"pack {pack} is empty"
        for k in keys:
            assert k in MODELS, f"pack {pack} references unknown model {k}"
    assert set(ri.MODEL_PACKS) == {"core", "all"}
    assert set(ri.MODEL_PACKS["core"]) <= set(ri.MODEL_PACKS["all"])


def test_guids_are_fixed_and_distinct():
    vals = list(ri.GUIDS.values())
    assert len(vals) == len(set(vals)), "installer AppIds must differ"
    for g in vals:
        assert re.fullmatch(r"\{[0-9A-F-]{36}\}", g)


def test_cli_contract():
    out = subprocess.run(
        [sys.executable, "-m", "backend.core.release_info", "version"],
        cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    assert out.returncode == 0
    assert out.stdout.strip() == ri.VERSION
