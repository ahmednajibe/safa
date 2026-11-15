"""M6 Task 0: per-stage results persisted in ``stages_json``.

When an op finishes, its stage entry gets
``{state:"done", actual_device, fallback_reason, message_code,
message_params, warning_code, warning_params, seconds}`` so a reconnect /
history view can show "Removed 2.9 s of silence in 3 places" without the
SSE race (M5 note). ``GET /api/jobs/{id}`` and every SSE ``state`` event
already carry ``stages`` — this file pins the new record shape end to end.
"""
import json
import threading
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from backend.core.settings import Settings
from backend.db import connect, init_db
from backend.jobs.store import JobStore
from backend.main import create_app
from backend.media.probe import probe

GENERATED = Path(__file__).parent / "fixtures" / "generated"
SPEECH_SIL = GENERATED / "speech_silences.wav"


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
        kind="full",
        ops=kw.pop("ops", []),
        output=kw.pop("output", {"audio_format": "wav", "keep_video": False}),
        preview=kw.pop("preview", None),
        device=kw.pop("device", "cpu"),
        audio_stream=kw.pop("audio_stream", 0),
        **kw,
    )


# ---- store level ---------------------------------------------------------------


def test_stages_created_pending(tmp_path):
    db = tmp_path / "app.db"
    init_db(db)
    _media(db, "m1", SPEECH_SIL)
    store = JobStore(db)
    j = _job(store, "m1", ops=[("silence", {}), ("loudness", {})])
    states = [s["state"] for s in j.stages]
    assert states == ["pending", "pending"]


def test_update_stage_merges_fields(tmp_path):
    db = tmp_path / "app.db"
    init_db(db)
    _media(db, "m1", SPEECH_SIL)
    store = JobStore(db)
    j = _job(store, "m1", ops=[("silence", {})])

    store.update_stage(j.id, "silence", state="running")
    s = store.get(j.id).stages[0]
    assert s["state"] == "running"
    assert s["id"] == "silence"  # untouched fields kept

    store.update_stage(
        j.id, "silence",
        state="done", actual_device="cpu", fallback_reason=None,
        message_code="silence_removed",
        message_params={"seconds": 1.2, "count": 3},
        warning_code=None, warning_params=None, seconds=0.75,
    )
    s = store.get(j.id).stages[0]
    assert s["state"] == "done"
    assert s["actual_device"] == "cpu"
    assert s["message_code"] == "silence_removed"
    assert s["message_params"] == {"seconds": 1.2, "count": 3}
    assert s["seconds"] == pytest.approx(0.75)


# ---- runner level --------------------------------------------------------------


def test_op_stage_final_progress_carries_seconds(tmp_path):
    """The op's closing Progress row carries wall ``stage_seconds``."""
    from backend.pipeline.runner import run_pipeline
    from backend.pipeline.types import OutputSpec

    p = probe(SPEECH_SIL)
    events = []
    run_pipeline(
        media_path=SPEECH_SIL,
        probe=p,
        audio_stream_index=p.audio[0].index,
        ops=[("silence", {})],
        output=OutputSpec(),
        job_root=tmp_path / "job",
        on_progress=events.append,
        should_cancel=lambda: False,
        device="cpu",
    )
    final = [
        e for e in events
        if e.stage == "Cut silence" and (e.stage_pct or 0) >= 1.0
    ]
    assert final, "no closing Progress row for the silence stage"
    assert final[-1].stage_seconds is not None
    assert final[-1].stage_seconds > 0
    assert final[-1].message_code == "silence_removed"


def test_fallback_reason_carried_on_progress(tmp_path, monkeypatch):
    """An OpResult fallback_reason reaches the closing Progress row."""
    import shutil

    from backend.operations.base import OpResult
    from backend.operations.impl import IMPLEMENTATIONS
    from backend.pipeline.runner import run_pipeline
    from backend.pipeline.types import OutputSpec

    def _fake(in_wav, params, ctx):
        out = ctx.work_dir / "stage_99_fakeop.wav"
        shutil.copyfile(in_wav, out)
        return OpResult(
            path=out, actual_device="cpu",
            fallback_reason="GPU out of memory",
        )

    monkeypatch.setitem(IMPLEMENTATIONS, "fakeop", _fake)
    p = probe(SPEECH_SIL)
    events = []
    run_pipeline(
        media_path=SPEECH_SIL,
        probe=p,
        audio_stream_index=p.audio[0].index,
        ops=[("fakeop", {})],
        output=OutputSpec(),
        job_root=tmp_path / "job",
        on_progress=events.append,
        should_cancel=lambda: False,
        device="gpu",
    )
    final = [e for e in events if e.stage == "fakeop"][-1]
    assert final.fallback_reason == "GPU out of memory"
    assert final.warning_code == "gpu_fallback"
    assert final.device == "cpu"


# ---- worker level (in-process) ----------------------------------------------------


def test_worker_persists_done_stage_records(tmp_path, monkeypatch):
    """A real job leaves a done record per op stage in stages_json."""
    from backend.worker import main as worker_main

    db = tmp_path / "app.db"
    data = tmp_path / "data"
    data.mkdir()
    init_db(db)
    monkeypatch.setenv("SOUND_SYSTEM_DB", str(db))
    monkeypatch.setenv("SOUND_SYSTEM_DATA", str(data))
    _media(db, "m1", SPEECH_SIL)
    store = JobStore(db)
    j = _job(store, "m1", ops=[("silence", {}), ("loudness", {})])

    assert worker_main([j.id]) == 0

    got = store.get(j.id)
    assert got.state == "completed"
    by_id = {s["id"]: s for s in got.stages}
    sil = by_id["silence"]
    assert sil["state"] == "done"
    assert sil["actual_device"] == "cpu"
    assert sil["message_code"] == "silence_removed"
    assert sil["message_params"]["seconds"] > 0
    assert sil["message_params"]["count"] >= 2
    assert sil["warning_code"] is None
    assert sil["seconds"] > 0
    loud = by_id["loudness"]
    assert loud["state"] == "done"
    assert loud["actual_device"] == "cpu"
    assert loud["seconds"] > 0


def test_worker_failed_job_marks_stage_failed(tmp_path, monkeypatch):
    """The op a job dies in is marked failed, not left pending."""
    from backend.operations.impl import IMPLEMENTATIONS
    from backend.worker import main as worker_main

    def _boom(in_wav, params, ctx):
        raise RuntimeError("boom")

    monkeypatch.setitem(IMPLEMENTATIONS, "loudness", _boom)
    db = tmp_path / "app.db"
    data = tmp_path / "data"
    data.mkdir()
    init_db(db)
    monkeypatch.setenv("SOUND_SYSTEM_DB", str(db))
    monkeypatch.setenv("SOUND_SYSTEM_DATA", str(data))
    _media(db, "m1", SPEECH_SIL)
    store = JobStore(db)
    j = _job(store, "m1", ops=[("silence", {}), ("loudness", {})])

    assert worker_main([j.id]) == 1

    by_id = {s["id"]: s for s in store.get(j.id).stages}
    assert by_id["silence"]["state"] == "done"
    assert by_id["loudness"]["state"] == "failed"


# ---- Review Focus 5: survives the job over the API ---------------------------------


def _client(tmp_path) -> TestClient:
    return TestClient(
        create_app(Settings(data_dir=tmp_path, db_path=tmp_path / "app.db"))
    )


def _collect_sse(c, jid: str, timeout: float = 240):
    got: list[tuple[str, object]] = []

    def _run():
        with c.stream("GET", f"/api/jobs/{jid}/events") as r:
            ev = None
            for line in r.iter_lines():
                if line.startswith("event:"):
                    ev = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    raw = line.split(":", 1)[1].strip()
                    try:
                        payload = json.loads(raw)
                    except json.JSONDecodeError:
                        payload = raw
                    got.append((ev, payload))
                    if ev == "done":
                        break

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout)
    assert not t.is_alive(), "SSE stream never sent 'done'"
    return got


def test_stage_summaries_survive_completed_job(tmp_path):
    """After completion ``GET /api/jobs/{id}`` (and the final SSE state)
    shows each stage's message_code/message_params/warning_code."""
    c = _client(tmp_path)
    with c:  # lifespan: real supervisor + worker subprocess
        r = c.post(
            "/api/media",
            content=SPEECH_SIL.read_bytes(),
            headers={
                "X-Filename": quote(SPEECH_SIL.name),
                "Content-Type": "application/octet-stream",
            },
        )
        mid = r.json()["media_id"]
        r = c.post(
            "/api/jobs",
            json={
                "media_id": mid,
                "device": "cpu",
                "ops": {"silence": {}, "loudness": {}},
                "output": {"audio_format": "wav", "keep_video": False},
            },
        )
        assert r.status_code == 201, r.text
        jid = r.json()["id"]
        events = _collect_sse(c, jid)
        assert events[-1][1]["state"] == "completed"

        final_stages = {
            s["id"]: s for s in events[-1][1]["stages"]
        }
        body = c.get(f"/api/jobs/{jid}").json()
        stages = {s["id"]: s for s in body["stages"]}
        for source in (final_stages, stages):
            sil = source["silence"]
            assert sil["state"] == "done"
            assert sil["message_code"] == "silence_removed"
            assert sil["message_params"]["seconds"] > 0
            assert sil["seconds"] > 0
            assert sil["actual_device"] == "cpu"
            assert "warning_code" in sil
            assert source["loudness"]["state"] == "done"
