"""M3b Task 2 tests: worker entry point run in-process.

``backend.worker.main([job_id])`` honours ``SOUND_SYSTEM_DB`` /
``SOUND_SYSTEM_DATA`` env overrides so tests can point it at a tmp store
(the supervisor passes the same env to the real subprocess).
"""
import json
from pathlib import Path

import pytest

from backend.db import connect, init_db
from backend.jobs.store import JobStore
from backend.media.probe import probe
from backend.worker import main as worker_main

GENERATED = Path(__file__).parent / "fixtures" / "generated"
ARABIC_WAV = GENERATED / "تسجيل اختبار 1.wav"


def _setup(tmp_path, monkeypatch):
    db = tmp_path / "app.db"
    data = tmp_path / "data"
    data.mkdir()
    init_db(db)
    monkeypatch.setenv("SOUND_SYSTEM_DB", str(db))
    monkeypatch.setenv("SOUND_SYSTEM_DATA", str(data))
    return db, data


def _media(db, media_id, path: Path):
    con = connect(db)
    try:
        con.execute(
            "INSERT INTO media (id, filename, stored_path, size_bytes,"
            " probe_json, created_at) VALUES (?,?,?,?,?,?)",
            (
                media_id, path.name, str(path), path.stat().st_size,
                json.dumps(probe(path).to_dict()), "2026-11-15T00:00:00",
            ),
        )
        con.commit()
    finally:
        con.close()


def _job(store, media_id, **kw):
    return store.create(
        media_id=media_id,
        kind=kw.pop("kind", "full"),
        ops=kw.pop("ops", []),
        output=kw.pop(
            "output", {"audio_format": "wav", "keep_video": False}
        ),
        preview=kw.pop("preview", None),
        device=kw.pop("device", "cpu"),
        audio_stream=kw.pop("audio_stream", 0),
        **kw,
    )


# ---- happy path ---------------------------------------------------------------


def test_worker_passthrough_completes(tmp_path, real_dir, monkeypatch):
    """Pass-through job on a real recording → completed + 1 audio file."""
    db, data = _setup(tmp_path, monkeypatch)
    src = real_dir / "Recording (2).m4a"
    _media(db, "m1", src)
    store = JobStore(db)
    j = _job(store, "m1", output={"audio_format": "mp3", "keep_video": False})

    rc = worker_main([j.id])

    assert rc == 0
    got = store.get(j.id)
    assert got.state == "completed"
    assert got.message == "Done"
    files = store.files(j.id)
    assert len(files) == 1
    assert files[0]["kind"] == "audio"
    assert files[0]["name"].endswith(".mp3")
    out = Path(files[0]["path"])
    assert out.is_file() and out.parent == data / "jobs" / j.id / "output"
    # progress rows were written while running
    assert got.total_stages and got.stage


def test_worker_preview_job(tmp_path, monkeypatch):
    db, data = _setup(tmp_path, monkeypatch)
    _media(db, "m1", ARABIC_WAV)
    store = JobStore(db)
    j = _job(
        store, "m1", kind="preview",
        preview={"start_s": 0.5, "end_s": 2.5},
    )
    rc = worker_main([j.id])
    assert rc == 0
    files = store.files(j.id)
    assert len(files) == 1 and "preview" in files[0]["name"]


# ---- failure / cancel ----------------------------------------------------------


def test_worker_failing_op_marks_failed(tmp_path, monkeypatch):
    """Review Focus 4: an op that raises → failed + short error + worker.log."""
    db, data = _setup(tmp_path, monkeypatch)
    _media(db, "m1", ARABIC_WAV)
    store = JobStore(db)
    j = _job(store, "m1", ops=[("silence", {})])

    rc = worker_main([j.id])

    assert rc == 1
    got = store.get(j.id)
    assert got.state == "failed"
    assert got.error and "silence" in got.error
    log = data / "jobs" / j.id / "worker.log"
    assert log.is_file()
    assert "NotImplementedError" in log.read_text(encoding="utf-8")


def test_worker_cancelled_before_start(tmp_path, monkeypatch):
    """cancel flag set → CancelledError path → state cancelled, output empty."""
    db, data = _setup(tmp_path, monkeypatch)
    _media(db, "m1", ARABIC_WAV)
    store = JobStore(db)
    j = _job(store, "m1")
    store.request_cancel(j.id)

    rc = worker_main([j.id])

    assert rc == 0
    got = store.get(j.id)
    assert got.state == "cancelled"
    outdir = data / "jobs" / j.id / "output"
    assert not outdir.exists() or not any(outdir.iterdir())


def test_worker_unknown_job_returns_1(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    assert worker_main(["no-such-job"]) == 1
