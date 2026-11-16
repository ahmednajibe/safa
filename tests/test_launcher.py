"""M10 T1: launcher checks — run the real PowerShell scripts.

- ``test_single_instance_launcher``: a stub server answering with our
  health JSON makes ``Get-PortState``/start.ps1 report "ours" (and the
  full non-CheckOnly path exits 0 without starting a second server); a
  foreign listener reports "busy"; a free port reports "free"; a
  non-integer SOUND_SYSTEM_PORT exits 2 with a bilingual message.
- ``test_stale_build_detection``: ``Test-StaleBuild`` against fake
  frontend trees in a temp dir — missing dist, stale src, stale config,
  fresh.
"""
from __future__ import annotations

import http.server
import os
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PS = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass"]
LIB = ROOT / "scripts" / "launcher-lib.ps1"
START = ROOT / "scripts" / "start.ps1"


def _run_ps(args: list[str], timeout: int = 60, env: dict | None = None):
    e = dict(os.environ)
    if env:
        e.update(env)
    return subprocess.run(
        PS + args, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
        timeout=timeout, cwd=str(ROOT), env=e,
    )


def _health_app(port_holder: list[int]):
    """Minimal HTTP server answering /api/health with our app's shape."""

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
    port_holder.append(srv.server_address[1])
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


# ---- port validation --------------------------------------------------------


def test_valid_port_rules():
    r = _run_ps(
        [
            "-Command",
            f". '{LIB}'; "
            "@(Test-ValidPort 'abc'; Test-ValidPort '80'; "
            "Test-ValidPort '65536'; Test-ValidPort '9000'; "
            "Test-ValidPort $null) -join ','",
        ]
    )
    # empty (invalid) → empty slots between commas: ,,9000,8790
    out = r.stdout.strip()
    assert out.endswith("9000,8790"), out
    assert r.returncode == 0, r.stderr


def test_invalid_env_port_exits_2_bilingual():
    r = _run_ps(
        ["-File", str(START), "-CheckOnly"],
        env={"SOUND_SYSTEM_PORT": "not-a-port"},
    )
    assert r.returncode == 2
    # Bilingual: the English half is always emitted.
    assert "SOUND_SYSTEM_PORT" in r.stdout


# ---- single instance ---------------------------------------------------------


def test_port_state_ours_busy_free():
    holder: list[int] = []
    srv = _health_app(holder)
    try:
        our_port = holder[0]
        busy = socket.socket()
        busy.bind(("127.0.0.1", 0))
        busy.listen(1)  # accepts nothing → health probe times out → busy
        busy_port = busy.getsockname()[1]
        free_port = _free_port()
        r = _run_ps(
            [
                "-Command",
                f". '{LIB}'; "
                f"Get-PortState {our_port}; "
                f"Get-PortState {busy_port}; "
                f"Get-PortState {free_port}",
            ],
            timeout=30,
        )
        states = r.stdout.strip().splitlines()
        assert [s.strip() for s in states] == ["ours", "busy", "free"]
    finally:
        srv.shutdown()
        busy.close()


def test_single_instance_launcher():
    """Full start.ps1 path: with our stub already answering, the launcher
    detects 'ours' and exits 0 — no second server is ever spawned."""
    holder: list[int] = []
    srv = _health_app(holder)
    try:
        t0 = time.monotonic()
        r = _run_ps(
            [
                "-File", str(START),
                "-Port", str(holder[0]), "-NoBrowser",
            ],
            timeout=60,
        )
        assert r.returncode == 0, r.stderr
        # Single-instance exits fast — never reaching the server spawn.
        assert time.monotonic() - t0 < 30
        assert "already running" in r.stdout
    finally:
        srv.shutdown()


def test_start_checkonly_states():
    holder: list[int] = []
    srv = _health_app(holder)
    try:
        r = _run_ps(
            [
                "-File", str(START), "-CheckOnly",
                "-Port", str(holder[0]),
            ],
            timeout=30,
        )
        assert r.returncode == 0
        assert "STATE:ours" in r.stdout
    finally:
        srv.shutdown()


# ---- stale build detection ----------------------------------------------------


def _ps_bool(expr: str) -> bool:
    r = _run_ps(
        ["-Command", f". '{LIB}'; {expr}"], timeout=30
    )
    out = r.stdout.strip()
    assert r.returncode == 0, r.stderr
    assert out in ("True", "False"), out
    return out == "True"


def _touch(path: Path, when: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x")
    os.utime(path, (when, when))


def test_stale_build_detection(tmp_path):
    root = tmp_path / "app"
    fe = root / "frontend"
    now = time.time()
    _touch(fe / "dist" / "index.html", now)
    _touch(fe / "src" / "main.tsx", now - 3600)
    _touch(fe / "vite.config.ts", now - 3600)
    _touch(fe / "package.json", now - 3600)
    assert _ps_bool(f"Test-StaleBuild '{root}'") is False

    # a newer src file → stale
    _touch(fe / "src" / "new.tsx", now + 10)
    assert _ps_bool(f"Test-StaleBuild '{root}'") is True
    _touch(fe / "src" / "new.tsx", now - 3600)

    # a newer tsconfig → stale
    _touch(fe / "tsconfig.app.json", now + 10)
    assert _ps_bool(f"Test-StaleBuild '{root}'") is True
    _touch(fe / "tsconfig.app.json", now - 3600)

    # missing dist → stale even with old sources
    (fe / "dist" / "index.html").unlink()
    assert _ps_bool(f"Test-StaleBuild '{root}'") is True


def test_need_uv_sync(tmp_path):
    root = tmp_path / "app"
    now = time.time()
    _touch(root / ".venv" / "pyvenv.cfg", now)
    _touch(root / "uv.lock", now - 3600)
    _touch(root / "pyproject.toml", now - 3600)
    assert _ps_bool(f"Test-NeedUvSync '{root}'") is False
    _touch(root / "uv.lock", now + 10)
    assert _ps_bool(f"Test-NeedUvSync '{root}'") is True
    (root / ".venv" / "pyvenv.cfg").unlink()
    assert _ps_bool(f"Test-NeedUvSync '{root}'") is True
