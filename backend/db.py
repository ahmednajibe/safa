"""SQLite access: WAL journal, foreign keys on, schema created at startup."""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS media (
    id TEXT PRIMARY KEY,
    filename TEXT,
    stored_path TEXT,
    size_bytes INTEGER,
    probe_json TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    media_id TEXT REFERENCES media(id),
    kind TEXT CHECK(kind IN ('full','preview')),
    ops_json TEXT,
    output_json TEXT,
    preview_json TEXT,
    state TEXT CHECK(state IN ('queued','running','completed','failed','cancelled','interrupted')),
    stage TEXT,
    stage_idx INTEGER,
    total_stages INTEGER,
    stage_pct REAL,
    overall_pct REAL,
    eta_sec REAL,
    device TEXT,
    message TEXT,
    error TEXT,
    worker_pid INTEGER,
    created_at TEXT,
    started_at TEXT,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS job_files (
    job_id TEXT REFERENCES jobs(id),
    name TEXT,
    kind TEXT,
    path TEXT,
    size_bytes INTEGER,
    PRIMARY KEY (job_id, name)
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = connect(db_path)
    try:
        con.executescript(SCHEMA)
        con.commit()
    finally:
        con.close()
