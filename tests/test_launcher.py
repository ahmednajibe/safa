"""M10 T1/M10c: launcher tests.

Launcher *logic* lives in ``backend.launcher`` (see
``test_launcher_module.py``). This file keeps the
``scripts/launcher-lib.ps1`` contract tests (the shared helpers
``package.ps1`` dot-sources: ``Test-ValidPort``, ``Get-PortState``,
``Test-StaleBuild``) plus one end-to-end probe through the real
``start.ps1`` shim → ``python -m backend.launcher``.
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


# ---- launcher-lib.ps1 contract ------------------------------------------------


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


def test_safa_launcher_stale_probe(tmp_path):
    """Test-SafaLauncherStale: True when Safa.exe is missing or older than
    Safa.cs — the packaging scripts rebuild only then."""
    src = tmp_path / "launcher"
    src.mkdir()
    cs = src / "Safa.cs"
    cs.write_text("// x")
    r_missing = _ps_bool(f"Test-SafaLauncherStale '{src}'")
    assert r_missing is True
    exe = src / "Safa.exe"
    now = time.time()
    exe.write_bytes(b"MZ")
    os.utime(exe, (now, now))
    os.utime(cs, (now - 60, now - 60))
    assert _ps_bool(f"Test-SafaLauncherStale '{src}'") is False
    os.utime(cs, (now + 60, now + 60))
    assert _ps_bool(f"Test-SafaLauncherStale '{src}'") is True


def test_stale_build_detection(tmp_path):
    root = tmp_path / "app"
    fe = root / "frontend"
    now = time.time()
    _touch(fe / "dist" / "index.html", now)
    _touch(fe / "src" / "main.tsx", now - 3600)
    _touch(fe / "vite.config.ts", now - 3600)
    _touch(fe / "package.json", now - 3600)
    assert _ps_bool(f"Test-StaleBuild '{root}'") is False
    _touch(fe / "src" / "App.tsx", now + 10)
    assert _ps_bool(f"Test-StaleBuild '{root}'") is True
    _touch(fe / "src" / "App.tsx", now - 3600)
    assert _ps_bool(f"Test-StaleBuild '{root}'") is False
    _touch(fe / "tsconfig.app.json", now + 20)
    assert _ps_bool(f"Test-StaleBuild '{root}'") is True


# ---- start.ps1 shim → backend.launcher ------------------------------------------


def test_shim_forwards_to_python_launcher():
    """start.ps1 -CheckOnly -Port <free> → STATE:free via the real
    backend.launcher (proves the PS→Python hand-off works)."""
    port = _free_port()
    r = _run_ps(
        ["-File", str(START), "-CheckOnly", "-Port", str(port)],
        timeout=60,
    )
    assert r.returncode == 0, r.stderr[-500:]
    assert "STATE:free" in r.stdout


def test_shim_invalid_env_port_exits_2():
    r = _run_ps(
        ["-File", str(START), "-CheckOnly"],
        env={"SOUND_SYSTEM_PORT": "not-a-port"},
    )
    assert r.returncode == 2
    assert "SOUND_SYSTEM_PORT" in r.stdout
