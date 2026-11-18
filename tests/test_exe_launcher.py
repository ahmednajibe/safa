"""M10c §3: Safa.exe lifecycle — compile, launch a stub child, kill the
launcher → the Job Object must reap the child (no orphan python).

The fake app tree is <tmp>/Safa.exe + <tmp>/python/python.exe (hardlink
to the dev interpreter) + <tmp>/backend/launcher.py stub — ``python -m
backend.launcher`` resolves the stub, which writes its pid then sleeps
forever, mimicking a running server.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "packaging" / "windows" / "launcher"
CSC = Path(
    os.environ.get("SystemRoot", r"C:\Windows")
) / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe"

pytestmark = pytest.mark.skipif(
    not CSC.is_file() or sys.platform != "win32",
    reason="needs .NET Framework csc.exe on Windows",
)

SLEEP_STUB = """\
import os, time
from pathlib import Path
Path(__file__).resolve().parents[1].joinpath("child.pid").write_text(
    str(os.getpid()))
time.sleep(300)
"""

EXIT_STUB = """\
raise SystemExit(0)
"""


def _compile(out_dir: Path) -> Path:
    exe = out_dir / "Safa.exe"
    icon = SRC / "safa.ico"
    args = [
        str(CSC), "/nologo", "/target:exe", "/platform:anycpu",
        "/codepage:65001",  # Safa.cs is BOM-less UTF-8 with Arabic literals
        f"/out:{exe}", "/optimize+",
    ]
    if icon.is_file():
        args.append(f"/win32icon:{icon}")
    args.append(str(SRC / "Safa.cs"))
    r = subprocess.run(args, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert exe.is_file()
    return exe


def _fake_app(tmp_path: Path, stub: str) -> Path:
    app = tmp_path / "Safa App عربي"  # Arabic + spaces must work
    (app / "python").mkdir(parents=True)
    (app / "backend").mkdir(parents=True)
    (app / "backend" / "__init__.py").write_text("")
    (app / "backend" / "launcher.py").write_text(stub)
    # The bundled python in real packages is a standalone interpreter —
    # .venv/Scripts/python.exe is a venv shim that dies without its
    # pyvenv.cfg, so fake the package with the base interpreter instead.
    real_py = Path(getattr(sys, "_base_executable", sys.executable))
    dst = app / "python" / "python.exe"
    try:
        os.link(real_py, dst)
    except OSError:
        shutil.copy(real_py, dst)
    # A lone python.exe only runs where its DLL + stdlib are findable via
    # PATH/registry (a dev box with a system install). Make the fake
    # package self-contained like a real one: DLLs beside the exe and a
    # ._pth pointing at the base stdlib plus the app root (`..`).
    base = real_py.parent
    for dll in base.glob("*.dll"):
        shutil.copy(dll, app / "python" / dll.name)
    (app / "python" / "python._pth").write_text(
        "\n".join([str(base / "Lib"), str(base / "DLLs"), ".."]) + "\n",
        encoding="utf-8",
    )
    return app


def _pid_alive(pid: int) -> bool:
    import psutil
    return psutil.pid_exists(pid)


def test_exe_launcher_lifecycle_job_kills_child(tmp_path):
    app = _fake_app(tmp_path, SLEEP_STUB)
    exe = _compile(app)
    # A file, not a PIPE: the child inherits stdout handles, so a pipe's
    # write end stays open until the child dies — .read() would block.
    out_path = app / "safa.out"
    with open(out_path, "w+", encoding="utf-8") as out:
        proc = subprocess.Popen(
            [str(exe)], cwd=str(app), stdout=out, stderr=subprocess.STDOUT)
        try:
            pid_file = app / "child.pid"
            deadline = time.monotonic() + 30
            while not pid_file.is_file() and time.monotonic() < deadline:
                time.sleep(0.2)
            assert pid_file.is_file(), "stub child never started"
            child_pid = int(pid_file.read_text().strip())
            assert _pid_alive(child_pid)
        finally:
            proc.kill()
            proc.wait(timeout=15)
    # Job Object: closing the launcher must reap the sleeping python.
    child_pid = int((app / "child.pid").read_text().strip())
    deadline = time.monotonic() + 10
    while _pid_alive(child_pid) and time.monotonic() < deadline:
        time.sleep(0.2)
    log = out_path.read_text(encoding="utf-8", errors="replace")
    if _pid_alive(child_pid):
        try:
            import psutil
            psutil.Process(child_pid).kill()  # don't leave the exe locked
        except Exception:
            pass
    assert not _pid_alive(child_pid), (
        f"child pid {child_pid} survived launcher death\n{log}")


def test_exe_launcher_exit_code_propagates(tmp_path):
    app = _fake_app(tmp_path, EXIT_STUB)
    exe = _compile(app)
    r = subprocess.run(
        [str(exe)], cwd=str(app), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=60,
    )
    assert r.returncode == 0, r.stdout[-500:]


def test_exe_launcher_missing_python_exits_4(tmp_path):
    app = tmp_path / "empty app"
    app.mkdir()
    exe = _compile(app)
    r = subprocess.run(
        [str(exe)], cwd=str(app), input="\n",
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=60,
    )
    assert r.returncode == 4
    assert "Python" in r.stdout
    assert "بايثون" in r.stdout  # Arabic line too — stdout is UTF-8
