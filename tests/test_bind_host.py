"""M10c: ``SOUND_SYSTEM_BIND_HOST`` — native default ``127.0.0.1``.

Only the container image sets ``0.0.0.0`` (compose still publishes
``127.0.0.1:8790:8790`` on the host). The Host/Origin allow-list is
unchanged by the bind address: even on ``0.0.0.0`` a LAN-scoped Host
header is rejected — only loopback authorities on the app port pass.

A non-loopback bind on a *native* install is almost always a mistake —
``create_app`` logs a loud warning at startup unless
``SOUND_SYSTEM_IN_CONTAINER=1`` marks the expected container bind.
"""
import logging
import subprocess
import sys

from fastapi.testclient import TestClient  # noqa: F401 (documented use)

from backend.core.settings import Settings
from backend.main import create_app
from tests.client import make_client


def _settings(env: dict[str, str]) -> str:
    """host as a fresh subprocess sees it (env read at construction)."""
    import os

    e = dict(os.environ)
    e.pop("SOUND_SYSTEM_BIND_HOST", None)
    e.update(env)
    out = subprocess.run(
        [
            sys.executable, "-c",
            "from backend.core.settings import Settings; print(Settings().host)",
        ],
        capture_output=True, text=True, env=e, check=True,
    )
    return out.stdout.strip()


def test_bind_host_default_loopback():
    assert _settings({}) == "127.0.0.1"


def test_bind_host_env_override_for_docker():
    assert _settings({"SOUND_SYSTEM_BIND_HOST": "0.0.0.0"}) == "0.0.0.0"


def test_bind_host_blank_falls_back():
    assert _settings({"SOUND_SYSTEM_BIND_HOST": "   "}) == "127.0.0.1"


def test_lan_host_header_still_rejected_on_wide_bind(tmp_path):
    """0.0.0.0 binds every interface; the guard must still refuse
    non-loopback Host authorities (defense in depth)."""
    app = create_app(Settings(
        host="0.0.0.0",
        data_dir=tmp_path, db_path=tmp_path / "app.db",
    ))
    c = make_client(app)
    assert c.get("/api/health").status_code == 200
    for host in ("192.168.1.10:8790", "safa.lan:8790", "0.0.0.0:8790"):
        r = c.get("/api/health", headers={"Host": host})
        assert r.status_code == 400, host
        assert r.json()["code"] == "bad_host", host
    for host in ("127.0.0.1:8790", "localhost:8790"):
        assert c.get("/api/health", headers={"Host": host}).status_code == 200


# ---- wide-bind startup warning --------------------------------------------------


def _mk(tmp_path, host):
    return create_app(Settings(
        host=host, data_dir=tmp_path, db_path=tmp_path / "app.db"))


def _wide_bind_warnings(caplog) -> list[str]:
    return [
        r.getMessage() for r in caplog.records
        if r.levelno >= logging.WARNING
        and "SOUND_SYSTEM_BIND_HOST" in r.getMessage()
    ]


def test_wide_bind_warns_on_native_install(tmp_path, caplog, monkeypatch):
    """0.0.0.0 on a native install must shout — silent LAN exposure is a
    footgun (the guard still applies; this is visibility, not policy)."""
    monkeypatch.delenv("SOUND_SYSTEM_IN_CONTAINER", raising=False)
    with caplog.at_level(logging.WARNING, logger="sound_system"):
        _mk(tmp_path, "0.0.0.0")
    msgs = _wide_bind_warnings(caplog)
    assert msgs, "expected a wide-bind warning"
    assert "0.0.0.0" in msgs[0]


def test_wide_bind_silent_inside_container(tmp_path, caplog, monkeypatch):
    """The Docker image binds 0.0.0.0 on purpose — SOUND_SYSTEM_IN_CONTAINER=1
    marks it intentional."""
    monkeypatch.setenv("SOUND_SYSTEM_IN_CONTAINER", "1")
    with caplog.at_level(logging.WARNING, logger="sound_system"):
        _mk(tmp_path, "0.0.0.0")
    assert _wide_bind_warnings(caplog) == []


def test_loopback_bind_never_warns(tmp_path, caplog, monkeypatch):
    monkeypatch.delenv("SOUND_SYSTEM_IN_CONTAINER", raising=False)
    with caplog.at_level(logging.WARNING, logger="sound_system"):
        for host in ("127.0.0.1", "localhost", "::1"):
            _mk(tmp_path, host)
    assert _wide_bind_warnings(caplog) == []


def test_dockerfiles_marks_container_bind():
    """Both images set SOUND_SYSTEM_IN_CONTAINER=1 so the wide bind is
    documented intent, not a warning."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    for df in ("Dockerfile", "Dockerfile.cuda"):
        text = (root / df).read_text(encoding="utf-8")
        assert "SOUND_SYSTEM_IN_CONTAINER=1" in text, df
