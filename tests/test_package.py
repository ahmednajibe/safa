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


def test_cpu_extra_has_no_cuda_deps():
    """M10c: one project, one lock — the `cpu` extra must not pull CUDA."""
    proj = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    deps = " ".join(proj["project"]["optional-dependencies"]["cpu"])
    assert "onnxruntime-gpu" not in deps
    assert "nvidia-" not in deps
    assert "+cu" not in deps
    assert 'onnxruntime==1.20.0' in deps
    assert 'torch==2.5.1' in deps
    # the separate packaging/cpu project is gone — one lock covers both
    assert not CPU_PROJ.exists()


# ---- Safa.exe build (compiled artifact, never committed) -----------------------


def test_launcher_exe_is_gitignored():
    """Compiled binaries must never be committed — Safa.exe is built."""
    r = subprocess.run(
        ["git", "check-ignore", "-v", "packaging/windows/launcher/Safa.exe"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert r.returncode == 0, (
        "packaging/windows/launcher/Safa.exe is not gitignored")


def test_build_launcher_script_exists_and_parses():
    ps1 = ROOT / "scripts" / "build-launcher.ps1"
    assert ps1.is_file(), "scripts/build-launcher.ps1 missing"
    r = subprocess.run(
        [
            "powershell", "-NoProfile", "-Command",
            "$e=$null;"
            "[System.Management.Automation.PSParser]::Tokenize("
            f"(Get-Content -Raw '{ps1}'),[ref]$e) > $null;"
            "$e.Count",
        ],
        capture_output=True, text=True, timeout=30,
    )
    assert r.stdout.strip() == "0", r.stderr + r.stdout


def test_build_scripts_use_build_launcher_not_inline_csc():
    """package.ps1 / build-installer.ps1 / release.yml build Safa.exe via
    scripts/build-launcher.ps1 — no inline csc anywhere."""
    pkg = (ROOT / "scripts" / "package.ps1").read_text(encoding="utf-8-sig")
    inst = (ROOT / "scripts" / "build-installer.ps1").read_text(
        encoding="utf-8-sig")
    rel = (ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8")
    for name, text in (("package.ps1", pkg), ("build-installer.ps1", inst),
                       ("release.yml", rel)):
        assert "build-launcher.ps1" in text, name
        assert "csc.exe" not in text, f"{name}: inline csc remains"


def test_launcher_stale_probe_in_launcher_lib():
    lib = (ROOT / "scripts" / "launcher-lib.ps1").read_text(
        encoding="utf-8-sig")
    assert "Test-SafaLauncherStale" in lib


def test_start_script_forwards_to_backend_launcher():
    """start.ps1 is a thin shim; the portable branch lives in
    backend/launcher.py (bundled python -> -m backend.launcher)."""
    src = (ROOT / "scripts" / "start.ps1").read_text(encoding="utf-8-sig")
    assert "python\\python.exe" in src
    assert "backend.launcher" in src
    launcher = (ROOT / "backend" / "launcher.py").read_text(encoding="utf-8")
    assert "_unblock_mark_of_the_web" in launcher
    assert "prefetch_models" in launcher
