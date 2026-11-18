"""Task 1/5 tests: SQLite schema, app factory, SPA mount."""
import contextlib
import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend.core.paths import ROOT
from backend.core.settings import Settings, get_settings
from backend.db import connect, init_db
from backend.main import create_app
from tests.client import make_client

DIST_INDEX = ROOT / "frontend" / "dist" / "index.html"
needs_dist = pytest.mark.skipif(
    not DIST_INDEX.exists(), reason="frontend/dist not built"
)


def _client(tmp_path):
    return make_client(
        create_app(Settings(data_dir=tmp_path, db_path=tmp_path / "app.db"))
    )


def _tables(db_path) -> set[str]:
    con = connect(db_path)
    try:
        rows = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        return {r["name"] for r in rows}
    finally:
        con.close()


def test_init_db_creates_tables(tmp_path):
    db = tmp_path / "app.db"
    init_db(db)
    names = _tables(db)
    assert {"media", "jobs", "job_files"} <= names


def test_init_db_idempotent(tmp_path):
    db = tmp_path / "app.db"
    init_db(db)
    init_db(db)  # second run must not raise or duplicate
    assert {"media", "jobs", "job_files"} <= _tables(db)


def test_connect_sets_wal_and_foreign_keys(tmp_path):
    db = tmp_path / "app.db"
    init_db(db)
    con = connect(db)
    try:
        assert con.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert con.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert con.row_factory is sqlite3.Row
    finally:
        con.close()


def test_settings_defaults(monkeypatch):
    monkeypatch.delenv("SOUND_SYSTEM_PORT", raising=False)
    s = Settings()
    assert s.host == "127.0.0.1"
    assert s.port == 8790
    assert s.retention_days == 7
    assert s.max_upload_mb == 4096
    assert get_settings() is get_settings()


def test_settings_port_env_override(monkeypatch):
    """SOUND_SYSTEM_PORT overrides the default 8790 (8765 belongs to another
    app on this machine)."""
    monkeypatch.setenv("SOUND_SYSTEM_PORT", "9123")
    assert Settings().port == 9123
    get_settings.cache_clear()
    try:
        assert get_settings().port == 9123
    finally:
        get_settings.cache_clear()


def test_create_app_initializes_db(tmp_path):
    settings = Settings(
        data_dir=tmp_path / "data", db_path=tmp_path / "data" / "app.db"
    )
    app = create_app(settings)
    assert app is not None
    assert settings.db_path.exists()
    assert {"media", "jobs", "job_files"} <= _tables(settings.db_path)


def test_app_instance_exists():
    from backend.main import app

    assert app is not None


# ---- SPA mount (Task 5 / Review Focus 1-3) -----------------------------------


@needs_dist
def test_spa_index_served(tmp_path):
    r = _client(tmp_path).get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert 'id="root"' in r.text
    # the shell names the hashed bundle: never let a browser keep an old one
    assert r.headers.get("cache-control") == "no-cache"


@needs_dist
def test_spa_fallback_route(tmp_path):
    """Unknown non-api routes get the SPA index (client-side routing)."""
    c = _client(tmp_path)
    for path in ("/history", "/jobs/abc123", "/deep/route/x"):
        r = c.get(path)
        assert r.status_code == 200, path
        assert 'id="root"' in r.text, path


def test_api_404_is_json(tmp_path):
    """Unknown /api/* must NOT be answered with index.html."""
    r = _client(tmp_path).get("/api/nope")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/json")


# ---- favicon allow-list (R3 §2) -------------------------------------------------


@contextlib.contextmanager
def _client_with_dist(tmp_path, files: dict[str, bytes]):
    """App whose FRONTEND_DIST is a fake dir holding only ``files``."""
    import backend.main as main_mod

    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text('<html><div id="root"></div></html>')
    for name, blob in files.items():
        (dist / name).write_bytes(blob)
    old = main_mod.FRONTEND_DIST
    main_mod.FRONTEND_DIST = dist
    try:
        app = create_app(
            Settings(
                data_dir=tmp_path / "d", db_path=tmp_path / "d" / "app.db"
            )
        )
        yield make_client(app)
    finally:
        main_mod.FRONTEND_DIST = old


def test_favicon_ico_and_svg_served(tmp_path):
    files = {
        "favicon.ico": b"\x00\x00\x01\x00fake",
        "favicon.svg": b"<svg></svg>",
        "apple-touch-icon.png": b"\x89PNG\r\n\x1a\nfake",
    }
    with _client_with_dist(tmp_path, files) as c:
        r = c.get("/favicon.ico")
        assert r.status_code == 200 and r.content == files["favicon.ico"]
        assert r.headers["content-type"] in (
            "image/x-icon", "image/vnd.microsoft.icon")
        r = c.get("/favicon.svg")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("image/svg")
        r = c.get("/apple-touch-icon.png")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("image/png")


def test_favicon_missing_file_still_404(tmp_path):
    with _client_with_dist(tmp_path, {}) as c:
        for p in ("/favicon.ico", "/favicon.svg", "/apple-touch-icon.png"):
            assert c.get(p).status_code == 404, p


def test_root_files_do_not_leak(tmp_path):
    """No generic root-file serving: repo files and traversal attempts
    get the SPA index (or 404), never file contents."""
    with _client_with_dist(tmp_path, {}) as c:
        for p in ("/pyproject.toml", "/..%2fpyproject.toml",
                  "/%2e%2e/pyproject.toml"):
            r = c.get(p)
            assert r.status_code in (200, 404), (p, r.status_code)
            assert b"[project]" not in r.content and b"name = " not in r.content


def test_missing_dist_fallback(tmp_path, monkeypatch):
    """Review Focus 2: dist missing → 200 plain HTML, API still works."""
    import backend.main as main_mod

    monkeypatch.setattr(main_mod, "FRONTEND_DIST", tmp_path / "no-such-dist")
    c = _client(tmp_path)
    r = c.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Frontend not built" in r.text
    assert "scripts/setup.ps1" in r.text
    assert c.get("/api/health").status_code == 200


def test_runs_from_other_cwd(tmp_path, monkeypatch):
    """Review Focus 1: started from another cwd, dist/data/tools still resolve."""
    monkeypatch.chdir(tmp_path)
    c = _client(tmp_path)
    assert c.get("/api/health").status_code == 200
    r = c.get("/")
    assert r.status_code == 200
    if DIST_INDEX.exists():
        assert 'id="root"' in r.text
    else:
        assert "Frontend not built" in r.text


def test_quiet_client_resets_only_drops_proactor_reset_noise():
    """The browser aborting a media range request made Windows' Proactor
    print 'Exception in callback ..._call_connection_lost ...
    ConnectionResetError [WinError 10054]' — harmless noise. Only that is
    dropped; every other loop error still reaches the default handler."""
    from backend import main as main_mod

    seen = []

    class Loop:
        def default_exception_handler(self, ctx):
            seen.append(ctx)

    noise = {
        "message": "Exception in callback _ProactorBasePipeTransport._call_connection_lost(None)",
        "exception": ConnectionResetError(10054, "forcibly closed"),
    }
    main_mod._quiet_client_resets(Loop(), noise)
    assert seen == []

    other_reset = {"message": "Task exception was never retrieved",
                   "exception": ConnectionResetError()}
    real_bug = {"message": "Exception in callback x()",
                "exception": RuntimeError("boom")}
    for ctx in (other_reset, real_bug):
        main_mod._quiet_client_resets(Loop(), ctx)
    assert seen == [other_reset, real_bug]
