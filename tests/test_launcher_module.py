"""M10c §3: launcher logic lives in ``backend.launcher`` (ported from
scripts/start.ps1). Pure probes are tested directly; the CLI surface is
tested in one subprocess.

- port validation: 1024–65535, default 8790, never trusts raw values
- single instance: health-identity probe → "ours" exits 0 without spawning
- port busy → 3; free → proceeds (stub servers)
- stale-build / uv-sync detection on fake trees
- bilingual output always emits the English line too
"""
from __future__ import annotations

import http.server
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from backend import launcher

ROOT = Path(__file__).resolve().parents[1]


# ---- fixtures ----------------------------------------------------------------


@pytest.fixture
def health_server():
    """Minimal server answering /api/health with our app's shape."""
    holder: list[int] = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = (
                b'{"status":"ok","python":"3.11.9","models":{}}'
                if self.path == "/api/health"
                else b"{}"
            )
            code = 200 if self.path == "/api/health" else 404
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    holder.append(srv.server_address[1])
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield holder[0]
    srv.shutdown()


@pytest.fixture
def foreign_listener():
    """A socket that listens but answers nothing → health probe fails."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    yield s.getsockname()[1]
    s.close()


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


# ---- port validation ----------------------------------------------------------


def test_valid_port_rules():
    assert launcher.valid_port(None) == 8790
    assert launcher.valid_port("") == 8790
    assert launcher.valid_port("  ") == 8790
    assert launcher.valid_port("9000") == 9000
    assert launcher.valid_port(9000) == 9000
    for bad in ("abc", "80", "65536", "-1", "9x9", "8790.5"):
        assert launcher.valid_port(bad) is None, bad


# ---- probes ---------------------------------------------------------------------


def test_port_state_ours_busy_free(health_server, foreign_listener):
    assert launcher.port_state(health_server) == "ours"
    assert launcher.port_state(foreign_listener) == "busy"
    assert launcher.port_state(_free_port()) == "free"


def test_is_our_app_rejects_foreign_json(health_server, foreign_listener):
    assert launcher.is_our_app(health_server)
    assert not launcher.is_our_app(foreign_listener)
    assert not launcher.is_our_app(_free_port())


# ---- single instance -----------------------------------------------------------


def test_single_instance_exits_0_no_spawn(health_server, capsys):
    """A running Safa → open the browser, exit 0, never spawn a second
    server (proved by returning before any server spawn could happen)."""
    t0 = time.monotonic()
    rc = launcher.main(
        ["--port", str(health_server), "--no-browser"])
    assert rc == 0
    assert time.monotonic() - t0 < 10
    out = capsys.readouterr().out
    assert "already running" in out


def test_port_busy_exits_3(foreign_listener, capsys):
    rc = launcher.main(["--port", str(foreign_listener), "--no-browser"])
    assert rc == 3
    out = capsys.readouterr().out
    assert "in use by another program" in out


def test_check_only_prints_state(health_server, capsys):
    rc = launcher.main(
        ["--port", str(health_server), "--check-only"])
    assert rc == 0
    assert "STATE:ours" in capsys.readouterr().out


def test_invalid_port_exits_2_bilingual(capsys, monkeypatch):
    monkeypatch.setenv("SOUND_SYSTEM_PORT", "not-a-port")
    rc = launcher.main([])
    assert rc == 2
    out = capsys.readouterr().out
    assert "SOUND_SYSTEM_PORT" in out
    assert "not-a-port" in out


def test_port_env_propagated(health_server, monkeypatch):
    """The validated port is written into the spawned server's env so the
    backend's Host allow-list can never disagree — without leaking into
    the launcher's own os.environ."""
    monkeypatch.delenv("SOUND_SYSTEM_PORT", raising=False)
    env = launcher._server_env(health_server)
    assert env["SOUND_SYSTEM_PORT"] == str(health_server)
    assert os.environ.get("SOUND_SYSTEM_PORT") is None


# ---- tree probes ---------------------------------------------------------------


def _touch(path: Path, when: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x")
    import os
    os.utime(path, (when, when))


def test_stale_build(tmp_path):
    root = tmp_path / "app"
    fe = root / "frontend"
    now = time.time()
    _touch(fe / "dist" / "index.html", now)
    for rel in ("src/main.tsx", "vite.config.ts", "package.json"):
        _touch(fe / rel, now - 3600)
    assert launcher.stale_build(root) is False
    _touch(fe / "src" / "App.tsx", now + 10)
    assert launcher.stale_build(root) is True
    _touch(fe / "src" / "App.tsx", now - 3600)
    assert launcher.stale_build(root) is False
    _touch(fe / "tsconfig.app.json", now + 20)
    assert launcher.stale_build(root) is True
    (fe / "dist" / "index.html").unlink()
    assert launcher.stale_build(root) is True


def test_need_uv_sync(tmp_path):
    root = tmp_path / "app"
    now = time.time()
    _touch(root / ".venv" / "pyvenv.cfg", now)
    _touch(root / "uv.lock", now - 100)
    _touch(root / "pyproject.toml", now - 100)
    assert launcher.need_uv_sync(root) is False
    _touch(root / "uv.lock", now + 10)
    assert launcher.need_uv_sync(root) is True
    (root / ".venv" / "pyvenv.cfg").unlink()
    assert launcher.need_uv_sync(root) is True


def test_find_python_portable_vs_dev(tmp_path):
    root = tmp_path / "app"
    (root / "python").mkdir(parents=True)
    py = root / "python" / ("python.exe" if sys.platform == "win32"
                          else "bin" / "python3")
    py.parent.mkdir(parents=True, exist_ok=True)
    py.write_text("x")
    assert launcher._find_python(root) == (py, True, False)


def test_find_python_dev_venv(tmp_path):
    root = tmp_path / "app"
    rel = (".venv/Scripts/python.exe" if sys.platform == "win32"
           else ".venv/bin/python")
    py = root / rel
    py.parent.mkdir(parents=True, exist_ok=True)
    py.write_text("x")
    assert launcher._find_python(root) == (py, False, False)


def test_find_python_broken_package(tmp_path):
    root = tmp_path / "app"
    (root / "python").mkdir(parents=True)
    _, portable, broken = launcher._find_python(root)
    assert broken is True


# ---- CLI surface (one subprocess probe) -----------------------------------------


def test_cli_check_only_free_port():
    port = _free_port()
    r = subprocess.run(
        [
            sys.executable, "-m", "backend.launcher",
            "--check-only", "--port", str(port),
        ],
        capture_output=True, text=True, cwd=str(ROOT), timeout=60,
    )
    assert r.returncode == 0
    assert f"STATE:free" in r.stdout


def test_cli_models_help_lists_import():
    r = subprocess.run(
        [sys.executable, "-m", "backend.launcher", "models", "--help"],
        capture_output=True, text=True, cwd=str(ROOT), timeout=60,
    )
    assert r.returncode == 0
    assert "import" in r.stdout


def test_cmd_start_does_not_leak_port_env(monkeypatch, capsys):
    """Regression: cmd_start must not write SOUND_SYSTEM_PORT into
    os.environ — later Settings() objects in the same process (the whole
    test session) would inherit the wrong port and the Host/Origin guard
    starts refusing 127.0.0.1:8790 requests."""
    monkeypatch.delenv("SOUND_SYSTEM_PORT", raising=False)
    args = launcher._build_parser().parse_args(
        ["--check-only", "--port", "8899"])
    launcher.cmd_start(args)
    assert os.environ.get("SOUND_SYSTEM_PORT") is None


def test_stale_build_ignores_test_files(tmp_path):
    """A new/edited *.test.tsx never reaches the bundle — it must not force
    a rebuild (v0.1.1: that rebuild then crashed on the npm lookup)."""
    root = tmp_path / "app"
    fe = root / "frontend"
    now = time.time()
    _touch(fe / "dist" / "index.html", now)
    _touch(fe / "src" / "main.tsx", now - 3600)
    _touch(fe / "src" / "components" / "HistoryDrawer.test.tsx", now + 10)
    assert launcher.stale_build(root) is False


def test_frontend_build_runs_npm_by_full_path():
    """On Windows npm is npm.cmd: shutil.which finds it via PATHEXT, but
    subprocess.run(["npm", ...]) raises WinError 2 — run the resolved path."""
    src = (Path(launcher.__file__)).read_text(encoding="utf-8")
    assert '["npm", "run", "build"]' not in src
    assert 'npm = shutil.which("npm")' in src
    assert "[npm, \"run\", \"build\"]" in src
