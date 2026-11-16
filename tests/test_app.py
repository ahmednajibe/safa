"""Task 1/5 tests: SQLite schema, app factory, SPA mount."""
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
