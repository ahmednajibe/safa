"""M3b Task 1 tests: JobStore over SQLite + jobs-table migration."""
import json
import sqlite3
import time
from pathlib import Path

import pytest

from backend.db import connect, init_db
from backend.jobs.models import TERMINAL, Job
from backend.jobs.store import JobStore
from backend.media.storage import new_id
from backend.pipeline.types import OutputFile, Progress


@pytest.fixture()
def db_path(tmp_path):
    p = tmp_path / "app.db"
    init_db(p)
    return p


@pytest.fixture()
def store(db_path):
    return JobStore(db_path)


def _media(db_path: Path, media_id: str = "m1") -> str:
    con = connect(db_path)
    try:
        con.execute(
            "INSERT OR IGNORE INTO media (id, filename, stored_path,"
            " size_bytes, probe_json, created_at) VALUES (?,?,?,?,?,?)",
            (media_id, "clip.mp4", "/x/original.mp4", 1000,
             json.dumps({"duration_s": 10.0}), "2026-11-15T00:00:00"),
        )
        con.commit()
    finally:
        con.close()
    return media_id


def _job(store, media_id="m1", **kw):
    return store.create(
        media_id=media_id,
        kind=kw.pop("kind", "full"),
        ops=kw.pop("ops", [("loudness", {"target": "podcast"})]),
        output=kw.pop("output", {"audio_format": "mp3", "keep_video": True}),
        preview=kw.pop("preview", None),
        device=kw.pop("device", "cpu"),
        audio_stream=kw.pop("audio_stream", 0),
        **kw,
    )


# ---- create / get / list -----------------------------------------------------


def test_create_get_roundtrip(store, db_path):
    _media(db_path)
    j = _job(store)
    assert isinstance(j, Job)
    got = store.get(j.id)
    assert got is not None
    assert got.media_id == "m1"
    assert got.state == "queued"
    assert got.kind == "full"
    assert got.requested_device == "cpu"
    assert got.audio_stream == 0
    assert got.ops == [{"id": "loudness", "params": {"target": "podcast"}}]
    assert got.output == {"audio_format": "mp3", "keep_video": True}
    assert got.preview is None
    assert got.retry_of is None
    assert got.created_at
    # snapshot stages: one entry per resolved op with an expected device
    # (M6: entries start "pending"; the worker writes the done record)
    assert got.stages == [
        {
            "id": "loudness",
            "name": "Even out volume",
            "device": "cpu",
            "fallback_reason": None,
            "state": "pending",
        }
    ]
    d = got.to_dict()
    assert json.dumps(d)  # JSON-serialisable


def test_get_missing_returns_none(store):
    assert store.get("nope") is None
    assert store.queue_position("nope") is None
    assert store.cancel_requested("nope") is False


def test_list_newest_first(store, db_path):
    _media(db_path)
    a = _job(store)
    b = _job(store)
    c = _job(store)
    ids = [j.id for j in store.list()]
    assert ids == [c.id, b.id, a.id]
    assert [j.id for j in store.list(limit=2)] == [c.id, b.id]


# ---- queue ordering ----------------------------------------------------------


def test_next_queued_oldest_and_queue_position(store, db_path):
    _media(db_path)
    a = _job(store)
    b = _job(store)
    c = _job(store)
    assert store.next_queued().id == a.id
    assert store.queue_position(a.id) == 1
    assert store.queue_position(b.id) == 2
    assert store.queue_position(c.id) == 3
    store.mark_running(a.id, pid=4321)
    assert store.next_queued().id == b.id
    assert store.queue_position(a.id) is None  # no longer queued
    got = store.get(a.id)
    assert got.state == "running" and got.started_at


# ---- progress throttling ------------------------------------------------------


def test_update_progress_throttled_but_stage_change_written(
    store, db_path
):
    _media(db_path)
    j = _job(store)
    store.mark_running(j.id, pid=1)
    # 10 rapid updates of the same stage → at most 2 actual writes and the
    # visible pct stays at the first written value.
    for i in range(10):
        store.update_progress(
            j.id,
            Progress(
                stage="Even out volume", stage_idx=1, total_stages=3,
                stage_pct=i / 10.0, overall_pct=i / 30.0,
            ),
        )
    row = connect(db_path).execute(
        "SELECT stage, stage_idx, total_stages, stage_pct, overall_pct"
        " FROM jobs WHERE id=?",
        (j.id,),
    ).fetchone()
    assert row["stage"] == "Even out volume"
    assert row["stage_idx"] == 1 and row["total_stages"] == 3
    assert row["stage_pct"] <= 0.1  # not the last update (0.9)

    # A stage change is always written immediately, even inside the window.
    store.update_progress(
        j.id,
        Progress(
            stage="Saving audio", stage_idx=2, total_stages=3,
            stage_pct=0.0, overall_pct=0.66,
        ),
    )
    row = connect(db_path).execute(
        "SELECT stage, stage_pct FROM jobs WHERE id=?", (j.id,)
    ).fetchone()
    assert row["stage"] == "Saving audio"

    # After the throttle window the next same-stage update lands.
    time.sleep(0.3)
    store.update_progress(
        j.id,
        Progress(
            stage="Saving audio", stage_idx=2, total_stages=3,
            stage_pct=0.99, overall_pct=0.99, device="cpu",
        ),
    )
    row = connect(db_path).execute(
        "SELECT stage_pct, device FROM jobs WHERE id=?", (j.id,)
    ).fetchone()
    assert row["stage_pct"] == 0.99


def test_update_progress_never_drops_important_rows(store, db_path):
    """R1 B8: stage completion (pct 1.0), a message_code, or a
    warning_code must land even inside the 250 ms throttle window —
    fast ops finish within it and their summaries must reach SSE."""
    _media(db_path)
    j = _job(store)
    store.mark_running(j.id, pid=1)
    # first write lands, starts the throttle window
    store.update_progress(
        j.id,
        Progress(stage="Cut silence", stage_idx=1, total_stages=3,
                 stage_pct=0.3, overall_pct=0.4),
    )
    # a mid-window pct-only row is dropped (throttle still works)
    store.update_progress(
        j.id,
        Progress(stage="Cut silence", stage_idx=1, total_stages=3,
                 stage_pct=0.5, overall_pct=0.5),
    )
    # the closing row lands anyway: pct 1.0 + summary code
    store.update_progress(
        j.id,
        Progress(stage="Cut silence", stage_idx=1, total_stages=3,
                 stage_pct=1.0, overall_pct=0.7,
                 message="Removed 1.0 s of silence in 2 places",
                 message_code="silence_removed",
                 message_params={"seconds": 1.0, "count": 2}),
    )
    row = connect(db_path).execute(
        "SELECT stage_pct, message_code, message_params_json"
        " FROM jobs WHERE id=?",
        (j.id,),
    ).fetchone()
    assert row["stage_pct"] == 1.0
    assert row["message_code"] == "silence_removed"
    assert json.loads(row["message_params_json"]) == {
        "seconds": 1.0, "count": 2,
    }

    # a warning row inside the window lands too
    store.update_progress(
        j.id,
        Progress(stage="Cut silence", stage_idx=1, total_stages=3,
                 stage_pct=0.9, overall_pct=0.9,
                 warning_code="gpu_fallback",
                 warning_params={"op_id": "silence"}),
    )
    row = connect(db_path).execute(
        "SELECT stage_pct FROM jobs WHERE id=?", (j.id,)
    ).fetchone()
    assert row["stage_pct"] == 0.9


# ---- cancel / finish ----------------------------------------------------------


def test_request_cancel_flag(store, db_path):
    _media(db_path)
    j = _job(store)
    assert store.cancel_requested(j.id) is False
    store.request_cancel(j.id)
    assert store.cancel_requested(j.id) is True


def test_finish_sets_state_error_message(store, db_path):
    _media(db_path)
    j = _job(store)
    store.finish(j.id, "failed", error="boom", message="Something went wrong")
    got = store.get(j.id)
    assert got.state == "failed"
    assert got.state in TERMINAL
    assert got.error == "boom"
    assert got.message == "Something went wrong"
    assert got.finished_at


def test_set_stage_device_records_fallback(store, db_path):
    _media(db_path)
    j = _job(store)
    store.set_stage_device(j.id, "loudness", "cpu", "oom")
    got = store.get(j.id)
    assert got.stages[0]["device"] == "cpu"
    assert got.stages[0]["fallback_reason"] == "oom"


# ---- files ---------------------------------------------------------------------


def test_add_file_and_files(store, db_path, tmp_path):
    _media(db_path)
    j = _job(store)
    out = tmp_path / "out.wav"
    out.write_bytes(b"1234")
    store.add_file(j.id, OutputFile("out.wav", "audio", out, 4))
    files = store.files(j.id)
    assert files[0]["name"] == "out.wav"
    assert files[0]["kind"] == "audio"
    assert files[0]["size_bytes"] == 4


def test_delete_removes_job_and_files(store, db_path, tmp_path):
    _media(db_path)
    j = _job(store)
    out = tmp_path / "o.wav"
    out.write_bytes(b"x")
    store.add_file(j.id, OutputFile("o.wav", "audio", out, 1))
    store.delete(j.id)
    assert store.get(j.id) is None
    assert store.files(j.id) == []


# ---- interrupted on startup (Review Focus 2) ------------------------------------


def test_mark_interrupted_on_startup(store, db_path):
    _media(db_path)
    running = _job(store)
    queued = _job(store)
    done = _job(store)
    store.mark_running(running.id, pid=99)
    store.finish(done.id, "completed")
    n = store.mark_interrupted_on_startup()
    assert n == 1
    r = store.get(running.id)
    assert r.state == "interrupted"
    assert r.message == "Stopped because the app was closed"
    assert store.get(queued.id).state == "queued"  # waits for the supervisor
    assert store.get(done.id).state == "completed"
    # second call is a no-op
    assert store.mark_interrupted_on_startup() == 0


# ---- terminal-state guard (R1 D: the mutation-probe survivor) -----------------


@pytest.mark.parametrize(
    "terminal", ["completed", "cancelled", "interrupted"]
)
def test_finish_after_terminal_is_noop(store, db_path, terminal):
    """A late write must not resurrect or overwrite a terminal job —
    finish() after completed/cancelled/interrupted is a no-op."""
    _media(db_path)
    j = _job(store)
    store.mark_running(j.id, pid=1)
    store.finish(j.id, terminal, message="first", error="first-err")
    first = store.get(j.id)
    # an orphan/late writer tries every plausible transition
    store.finish(j.id, "completed", message="late-done")
    store.finish(j.id, "failed", error="late-err")
    store.finish(j.id, "cancelled", message="late-cancel")
    got = store.get(j.id)
    assert got.state == terminal
    assert got.message == first.message
    assert got.error == first.error
    assert got.finished_at == first.finished_at


def test_orphan_worker_completed_cannot_overwrite_interrupted(
    store, db_path
):
    """The killed worker's late ``completed`` lands after the server
    already marked the job interrupted — the terminal row must win."""
    _media(db_path)
    j = _job(store)
    store.mark_running(j.id, pid=1)
    store.finish(j.id, "interrupted", message="server closed")
    store.finish(j.id, "completed", message="Done")
    assert store.get(j.id).state == "interrupted"


def test_failed_can_still_be_cancelled(store, db_path):
    """The one allowed transition: a worker that 'failed' because its
    ffmpeg was killed is marked cancelled, not left failed."""
    _media(db_path)
    j = _job(store)
    store.mark_running(j.id, pid=1)
    store.finish(j.id, "failed", error="ffmpeg died")
    store.finish(j.id, "cancelled", message="Cancelled")
    assert store.get(j.id).state == "cancelled"


# ---- migration on an M2-era database --------------------------------------------


_M2_JOBS_SCHEMA = """
CREATE TABLE jobs (
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
"""


def test_migration_idempotent_on_m2_db(tmp_path):
    """A DB created by M2's init_db gains the new columns and keeps its rows."""
    db = tmp_path / "app.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db))
    con.execute(
        "CREATE TABLE media (id TEXT PRIMARY KEY, filename TEXT,"
        " stored_path TEXT, size_bytes INTEGER, probe_json TEXT,"
        " created_at TEXT)"
    )
    con.execute(_M2_JOBS_SCHEMA)
    con.execute(
        "INSERT INTO jobs (id, media_id, kind, state) VALUES"
        " ('old1','m','full','completed')"
    )
    con.commit()
    con.close()

    init_db(db)
    init_db(db)  # second run must be a no-op

    cols = {
        r[1]
        for r in connect(db).execute("PRAGMA table_info(jobs)").fetchall()
    }
    for col in (
        "requested_device",
        "audio_stream",
        "cancel_requested",
        "stages_json",
        "retry_of",
    ):
        assert col in cols
    row = connect(db).execute(
        "SELECT state FROM jobs WHERE id='old1'"
    ).fetchone()
    assert row[0] == "completed"
    # and the store works on the migrated DB
    store = JobStore(db)
    assert store.get("old1") is not None
