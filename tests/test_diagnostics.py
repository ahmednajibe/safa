"""M10 T7: rotating app.log, bounded worker.log, redacted diagnostics."""
import getpass
import json
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from backend.core.logs import (
    APP_LOG_BACKUPS,
    APP_LOG_MAX,
    app_log_path,
    bound_log,
    collect_diagnostics,
    redact_text,
    setup_app_logging,
)
from backend.core.settings import Settings
from backend.db import connect, init_db
from backend.jobs.store import JobStore
from backend.main import create_app
from tests.client import make_client


def _app(tmp_path):
    data = tmp_path / "data"
    settings = Settings(data_dir=data, db_path=data / "app.db")
    return create_app(settings), settings


def _media_row(db: Path, mid: str, filename: str):
    con = connect(db)
    try:
        con.execute(
            "INSERT INTO media (id, filename, stored_path, size_bytes,"
            " probe_json, created_at) VALUES (?,?,?,?,?,?)",
            (mid, filename, str(Path("x") / filename), 1,
             "{}", "2026-01-01T00:00:00+00:00"),
        )
        con.commit()
    finally:
        con.close()


def test_app_log_created_and_writable(tmp_path):
    _, settings = _app(tmp_path)
    path = app_log_path(settings.data_dir)
    assert path.parent.is_dir()
    import logging

    logging.getLogger("sound_system").info("hello diagnostics")
    # force flush
    for h in logging.getLogger("sound_system").handlers:
        h.flush()
    assert path.is_file()
    assert "hello diagnostics" in path.read_text(encoding="utf-8")


def test_app_log_handler_is_rotating(tmp_path):
    _, settings = _app(tmp_path)
    path = app_log_path(settings.data_dir)
    import logging

    handler = next(
        h
        for h in logging.getLogger("sound_system").handlers
        if isinstance(h, RotatingFileHandler)
    )
    assert handler.maxBytes == APP_LOG_MAX
    assert handler.backupCount == APP_LOG_BACKUPS
    assert Path(handler.baseFilename) == path


def test_setup_app_logging_idempotent(tmp_path):
    data = tmp_path / "data"
    p1 = setup_app_logging(data)
    import logging

    n = len(logging.getLogger("sound_system").handlers)
    p2 = setup_app_logging(data)
    assert p1 == p2
    assert len(logging.getLogger("sound_system").handlers) == n


def test_bound_log_truncates_head(tmp_path):
    p = tmp_path / "worker.log"
    p.write_bytes(b"x" * 3000)
    bound_log(p, cap=1000)
    data = p.read_bytes()
    assert len(data) <= 1000
    assert data.startswith(b"... [earlier log lines dropped]")


def test_bound_log_noop_under_cap(tmp_path):
    p = tmp_path / "worker.log"
    p.write_bytes(b"short")
    bound_log(p, cap=1000)
    assert p.read_bytes() == b"short"


def test_redact_text_covers_paths_and_names():
    secrets = ["C:\\Users\\أحمد", "secret-recording.m4a"]
    t = redact_text(
        "read C:\\Users\\أحمد\\sound_system\\secret-recording.m4a failed",
        secrets,
    )
    assert "أحمد" not in t
    assert "secret-recording" not in t
    assert "<redacted>" in t


def test_diagnostics_redaction(tmp_path):
    """No user path, account name or media filename may appear anywhere
    in the diagnostics payload (plan: ``test_diagnostics_redaction``)."""
    app, settings = _app(tmp_path)
    store = app.state.store
    secret_name = "سارة's private take 2024.m4a"
    _media_row(settings.db_path, "m1", secret_name)

    log = app_log_path(settings.data_dir)
    userprofile = os.environ.get("USERPROFILE") or str(Path.home())
    username = getpass.getuser()
    log.write_text(
        f"uploaded {secret_name}\n"
        f"tmp fell back to {userprofile}\\AppData\\Local\\Temp\n"
        f"user {username} ran a job\n",
        encoding="utf-8",
    )
    jdir = settings.data_dir / "jobs" / "j1"
    jdir.mkdir(parents=True)
    (jdir / "worker.log").write_text(
        f"traceback while reading {secret_name} at {userprofile}\n",
        encoding="utf-8",
    )

    payload = collect_diagnostics(settings, store)
    blob = json.dumps(payload, ensure_ascii=False)
    assert secret_name not in blob
    assert userprofile not in blob
    assert userprofile.replace("\\", "/") not in blob
    assert username not in blob
    assert "<redacted>" in blob
    # and through the API
    client = make_client(app)
    r = client.get("/api/diagnostics")
    assert r.status_code == 200
    blob = json.dumps(r.json(), ensure_ascii=False)
    assert secret_name not in blob
    assert userprofile not in blob
    assert username not in blob


def test_diagnostics_shape(tmp_path):
    app, settings = _app(tmp_path)
    app_log_path(settings.data_dir).write_text("line1\nline2\n")
    r = make_client(app).get("/api/diagnostics")
    assert r.status_code == 200
    d = r.json()
    for key in (
        "app",
        "generated_at",
        "python",
        "device",
        "modules",
        "models",
        "settings",
        "recent_jobs",
        "log_tail",
    ):
        assert key in d, key
    assert d["log_tail"] == ["line1", "line2"]
    # ffmpeg path must not leak (could sit under the user profile)
    assert "path" not in (d.get("ffmpeg") or {})
    # jobs carry no media filename
    for j in d["recent_jobs"]:
        assert "filename" not in j


def test_worker_log_tail_is_newest(tmp_path):
    _, settings = _app(tmp_path)
    old = settings.data_dir / "jobs" / "a"
    new = settings.data_dir / "jobs" / "b"
    old.mkdir(parents=True)
    new.mkdir(parents=True)
    (old / "worker.log").write_text("old job\n")
    w = new / "worker.log"
    w.write_text("new job\n")
    os.utime(old / "worker.log", (0, 0))
    store = JobStore(settings.db_path)
    d = collect_diagnostics(settings, store)
    assert d["worker_log_tail"] == ["new job"]


def test_diagnostics_requires_valid_host(tmp_path):
    app, _ = _app(tmp_path)
    client = make_client(app)
    r = client.get(
        "/api/diagnostics", headers={"host": "evil.example.com"}
    )
    assert r.status_code == 400
