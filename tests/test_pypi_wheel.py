"""safa-audio bootstrap wheel (M10d wave 2 §9).

Static + unit coverage: project metadata stays in sync with the root
pyproject, the CLI resolves data dirs/devices/commands correctly, the
built wheel is small and carries only the whitelisted payload, and the
frozen locks keep their hashes.

The real end-to-end run (fresh venv → safa setup --device cpu → health →
Podcast job) is ``test_wheel_e2e`` — marked slow because it provisions
the ~2 GB CPU runtime into a scratch data dir.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "packaging" / "pypi"
sys.path.insert(0, str(PKG))

from safa_bootstrap import cli  # noqa: E402

PYPROJECT = PKG / "pyproject.toml"


# ---- metadata sync -------------------------------------------------------------


def _root_version() -> str:
    import tomllib
    return tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))[
        "project"]["version"]


def test_pyproject_metadata():
    import tomllib
    data = tomllib.loads(PYPROJECT.read_text("utf-8"))
    proj = data["project"]
    assert proj["name"] == "safa-audio"
    assert proj["version"] == _root_version()
    assert proj["dependencies"] == []  # stdlib-only bootstrap
    assert "3.11" in proj["requires-python"]
    scripts = proj["scripts"]
    assert scripts["safa"] == "safa_bootstrap.cli:main"
    assert scripts["safa-audio"] == "safa_bootstrap.cli:main"


def test_bootstrap_version_matches_root():
    import safa_bootstrap
    assert safa_bootstrap.__version__ == _root_version()


# ---- cli unit tests -------------------------------------------------------------


def test_data_dir_env_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("SOUND_SYSTEM_DATA_DIR", str(tmp_path / "اختيار"))
    assert cli.data_dir() == tmp_path / "اختيار"


@pytest.mark.parametrize("plat", ["win32", "darwin", "linux"])
def test_data_dir_platform_defaults(monkeypatch, tmp_path, plat):
    monkeypatch.delenv("SOUND_SYSTEM_DATA_DIR", raising=False)
    monkeypatch.setattr(sys, "platform", plat)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "la"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    d = cli.data_dir()
    if plat == "win32":
        assert d == tmp_path / "la" / "Safa"
    elif plat == "darwin":
        assert d == tmp_path / "home" / "Library" / \
            "Application Support" / "Safa"
    else:
        assert d == tmp_path / "xdg" / "safa-data"


def test_resolve_device_explicit_and_darwin(monkeypatch):
    assert cli.resolve_device("cpu") == "cpu"
    monkeypatch.setattr(cli, "detect_nvidia", lambda: True)
    assert cli.resolve_device("auto") == "cu124"
    monkeypatch.setattr(cli, "detect_nvidia", lambda: False)
    assert cli.resolve_device("auto") == "cpu"
    monkeypatch.setattr(sys, "platform", "darwin")
    assert cli.resolve_device("auto") == "cpu"
    assert cli.resolve_device("cuda") == "cpu"  # no CUDA on macOS


def test_detect_nvidia_driver_threshold(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda n: "/x/nvidia-smi")

    class R:
        stdout = "580.65\n"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: R())
    assert cli.detect_nvidia() is True

    class R2:
        stdout = "470.1\n"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: R2())
    assert cli.detect_nvidia() is False
    monkeypatch.setattr(shutil, "which", lambda n: None)
    assert cli.detect_nvidia() is False


def test_install_deps_uses_hashes_and_torch_index(monkeypatch, tmp_path):
    app = tmp_path / "app"
    (app / "python" / "Scripts").mkdir(parents=True)
    py = app / "python" / "Scripts" / "python.exe"
    py.write_bytes(b"x")
    (app / "requirements-cpu.txt").write_text("torch==2.5.1 --hash=sha256:x\n")
    calls = []
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: calls.append(a[0]))
    cli.install_deps(app, "cpu", uv="uv")
    cmd = calls[0]
    assert cmd[0] == "uv" and "--require-hashes" in cmd
    assert "https://download.pytorch.org/whl/cpu" in cmd
    cli.install_deps(app, "cu124", uv=None)
    cmd = calls[1]
    assert cmd[0] == str(py) and "-m" in cmd and "pip" in cmd
    assert "--require-hashes" in cmd
    assert "https://download.pytorch.org/whl/cu124" in cmd


def test_stage_payload_whitelist(tmp_path):
    sys.path.insert(0, str(ROOT / "scripts"))
    import importlib
    bw = importlib.import_module("build-wheel")
    app = bw.stage_payload(tmp_path / "app")
    names = {p.relative_to(app).as_posix() for p in app.rglob("*") if p.is_file()}
    assert "backend/main.py" in names
    assert "frontend/dist/index.html" in names
    assert "release-info.toml" in names
    assert "scripts/prefetch_models.py" in names
    forbidden = [n for n in names if any(
        bad in n for bad in
        ("models/", "data/", "fixtures", ".orchestrator", "node_modules",
         "graphify-out", "tools/ffmpeg", "__pycache__"))]
    assert not forbidden, forbidden


def test_export_requirements_keeps_hashes(tmp_path):
    if shutil.which("uv") is None:
        pytest.skip("uv not on PATH")
    sys.path.insert(0, str(ROOT / "scripts"))
    import importlib
    bw = importlib.import_module("build-wheel")
    reqs = bw.export_requirements(tmp_path)
    assert {p.name for p in reqs} == {
        "requirements-cpu.txt", "requirements-cu124.txt"}
    text = reqs[0].read_text("utf-8")
    assert "--hash=" in text
    assert "torch" in text


# ---- wheel build + contents -------------------------------------------------------


def test_wheel_build_and_contents(tmp_path):
    """uv build → the wheel is small and carries only the payload."""
    if shutil.which("uv") is None:
        pytest.skip("uv not on PATH")
    out = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "build-wheel.py"),
         "--out-dir", str(tmp_path)],
        capture_output=True, text=True, timeout=600, cwd=ROOT)
    assert out.returncode == 0, out.stdout + out.stderr
    wheels = list(tmp_path.glob("safa_audio-*.whl"))
    assert len(wheels) == 1, out.stdout
    whl = wheels[0]
    assert whl.stat().st_size < 64 * 1024 * 1024, "bootstrap wheel must be small"
    with zipfile.ZipFile(whl) as z:
        names = z.namelist()
        assert "safa_bootstrap/cli.py" in names
        assert "safa_bootstrap/app/backend/main.py" in names
        assert "safa_bootstrap/app/frontend/dist/index.html" in names
        assert "safa_bootstrap/app/requirements-cpu.txt" in names
        assert "safa_bootstrap/app/requirements-cu124.txt" in names
        assert "safa_bootstrap/app/release-info.toml" in names
        for bad in ("models/", "data/", "fixtures", ".orchestrator",
                    "node_modules", "graphify-out", "tools/ffmpeg"):
            assert not any(bad in n for n in names), bad
        ep = z.read(
            [n for n in names if n.endswith("entry_points.txt")][0]).decode()
        assert "safa" in ep and "safa_bootstrap.cli:main" in ep
        cpu = z.read("safa_bootstrap/app/requirements-cpu.txt").decode()
        assert "--hash=" in cpu


# ---- real end-to-end (slow) --------------------------------------------------------


@pytest.mark.slow
def test_wheel_e2e_setup_health_job(tmp_path):
    """Real acceptance (M10d §1): wheel → fresh venv → setup → health →
    Podcast job. Needs internet for the runtime; reuses the repo models/
    tree via SOUND_SYSTEM_MODELS_DIR so nothing re-downloads."""
    if shutil.which("uv") is None:
        pytest.skip("uv required")
    scratch = ROOT / "data" / "tmp" / "wheel-e2e"
    shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir(parents=True)
    env = dict(os.environ)
    env["SOUND_SYSTEM_DATA_DIR"] = str(scratch / "user data تجربة")
    env["UV_CACHE_DIR"] = str(scratch / "uv-cache")
    env["SOUND_SYSTEM_MODELS_DIR"] = str(ROOT / "models")
    env["PYTHONUTF8"] = "1"

    # build + install the wheel into a fresh venv
    wheel_dir = scratch / "dist"
    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "build-wheel.py"),
         "--out-dir", str(wheel_dir)], check=True, cwd=ROOT, env=env)
    whl = next(wheel_dir.glob("safa_audio-*.whl"))
    venv = scratch / "venv"
    subprocess.run(["uv", "venv", str(venv)], check=True, env=env)
    vpy = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    subprocess.run(
        ["uv", "pip", "install", "--python", str(vpy), str(whl)],
        check=True, env=env)
    safa = venv / ("Scripts/safa.exe" if os.name == "nt" else "bin/safa")
    assert safa.is_file()

    # setup provisions the private runtime (uv path; cpu variant)
    subprocess.run(
        [str(safa), "setup", "--device", "cpu"],
        check=True, env=env, timeout=3600)
    data = Path(env["SOUND_SYSTEM_DATA_DIR"])
    assert (data / "runtime" / "app" / "installed.json").is_file()

    # launch → health → Podcast job → stop
    sys.path.insert(0, str(ROOT / "scripts"))
    import importlib
    smoke = importlib.import_module("smoke_package")
    port = 8796
    log = (scratch / "start.log").open("w", encoding="utf-8",
                                       errors="replace")
    proc = subprocess.Popen(
        [str(safa), "start", "--no-browser", "--port", str(port)],
        stdout=log, stderr=subprocess.STDOUT, env=env)
    try:
        health = smoke.wait_health(f"http://127.0.0.1:{port}", 300.0)
        assert health["status"] == "ok"
        job = smoke.run_podcast_job(
            f"http://127.0.0.1:{port}", smoke.FIXTURE, "cpu", 900.0)
        assert job["state"] == "completed", job
    finally:
        log.close()
        if proc.poll() is None:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)]
                           if os.name == "nt" else ["kill", str(proc.pid)],
                           capture_output=True)
