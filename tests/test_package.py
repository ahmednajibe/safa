"""M10b: portable-package scaffolding — fast checks that the builder,
smoke script and CPU wheel-set exist and stay consistent. The real
end-to-end proof lives in ``test_package_smoke.py`` (slow marker)."""
from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_PS1 = ROOT / "scripts" / "package.ps1"
SMOKE_PY = ROOT / "scripts" / "smoke_package.py"
CPU_PROJ = ROOT / "packaging" / "cpu" / "pyproject.toml"


def test_package_script_exists_and_parses():
    assert PACKAGE_PS1.is_file()
    r = subprocess.run(
        [
            "powershell", "-NoProfile", "-Command",
            "$e=$null;"
            "[System.Management.Automation.PSParser]::Tokenize("
            f"(Get-Content -Raw '{PACKAGE_PS1}'),[ref]$e) > $null;"
            "$e.Count",
        ],
        capture_output=True, text=True, timeout=30,
    )
    assert r.stdout.strip() == "0", r.stderr + r.stdout


def test_smoke_script_exists():
    assert SMOKE_PY.is_file()


def test_dist_pkg_gitignored():
    assert "dist_pkg/" in (ROOT / ".gitignore").read_text(
        encoding="utf-8"
    ).splitlines()


def test_cpu_project_has_no_cuda_deps():
    assert CPU_PROJ.is_file()
    proj = tomllib.loads(CPU_PROJ.read_text(encoding="utf-8"))
    deps = " ".join(proj["project"]["dependencies"])
    assert "onnxruntime-gpu" not in deps
    assert "nvidia-" not in deps
    assert "+cu" not in deps
    assert 'onnxruntime==1.20.0' in deps
    assert 'torch==2.5.1' in deps
    # the separate project is locked for reproducible builds
    assert (CPU_PROJ.parent / "uv.lock").is_file()


def test_start_script_has_portable_branch():
    src = (ROOT / "scripts" / "start.ps1").read_text(encoding="utf-8-sig")
    assert "python\\python.exe" in src
    assert "Unblock-File" in src
    assert "prefetch_models.py" in src
