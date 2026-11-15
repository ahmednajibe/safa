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
    finished_at TEXT,
    requested_device TEXT,
    audio_stream INT,
    cancel_requested INT DEFAULT 0,
    stages_json TEXT,
    retry_of TEXT
);
CREATE TABLE IF NOT EXISTS job_files (
    job_id TEXT REFERENCES jobs(id),
    name TEXT,
    kind TEXT,
    path TEXT,
    size_bytes INTEGER,
    PRIMARY KEY (job_id, name)
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


# Columns added after M2; applied to older DBs via ALTER TABLE (idempotent).
# M3b set, then M5's message-code columns (SPEC §11).
_JOBS_NEW_COLUMNS = (
    ("requested_device", "TEXT"),
    ("audio_stream", "INT"),
    ("cancel_requested", "INT DEFAULT 0"),
    ("stages_json", "TEXT"),
    ("retry_of", "TEXT"),
    ("message_code", "TEXT"),
    ("message_params_json", "TEXT"),
    ("error_code", "TEXT"),
    ("error_params_json", "TEXT"),
    ("warnings_json", "TEXT"),
    ("stage_id", "TEXT"),
    ("eta_sec", "REAL"),
)

# M9a Task 0: where each output was auto-copied (SPEC §12), NULL when the
# app copy in data/jobs/<id>/output is the only one.
_JOB_FILES_NEW_COLUMNS = (
    ("saved_path", "TEXT"),
)


def _migrate(con: sqlite3.Connection) -> None:
    existing = {r[1] for r in con.execute("PRAGMA table_info(jobs)")}
    for name, ddl in _JOBS_NEW_COLUMNS:
        if name not in existing:
            con.execute(f"ALTER TABLE jobs ADD COLUMN {name} {ddl}")
    existing = {r[1] for r in con.execute("PRAGMA table_info(job_files)")}
    for name, ddl in _JOB_FILES_NEW_COLUMNS:
        if name not in existing:
            con.execute(f"ALTER TABLE job_files ADD COLUMN {name} {ddl}")


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = connect(db_path)
    try:
        con.executescript(SCHEMA)
        _migrate(con)
        con.commit()
    finally:
        con.close()
