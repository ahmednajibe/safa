"""M9a Task 0 tests: backend prerequisites for the UI.

1. Processed peaks written at encode + ``GET /api/jobs/{id}/peaks``.
2. HTTP Range support on ``/api/jobs/{id}/files/{name}``.
3. ``files`` on ``GET /api/jobs`` rows and every SSE ``state`` event.
4. ``stage_id`` (op id / decode / encode / video) on progress + payloads.
5. ``eta_sec``: estimates × remaining stages, refined by measured seconds.
"""
import json
import threading
import time
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from backend.core.settings import Settings
from backend.db import connect, init_db
from backend.jobs.store import JobStore
from backend.main import create_app
from backend.media.probe import probe
from backend.pipeline.runner import run_pipeline
from backend.pipeline.types import OutputFile, OutputSpec, Progress

GENERATED = Path(__file__).parent / "fixtures" / "generated"
ARABIC_WAV = GENERATED / "تسجيل اختبار 1.wav"
CLIP = GENERATED / "clip.mp4"


def _client(tmp_path) -> TestClient:
    return TestClient(
        create_app(Settings(data_dir=tmp_path, db_path=tmp_path / "app.db"))
    )


def _upload(client, path: Path, name: str | None = None) -> str:
    r = client.post(
        "/api/media",
        content=path.read_bytes(),
        headers={
            "X-Filename": quote(name or path.name),
            "Content-Type": "application/octet-stream",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["media_id"]


def _post_job(c, media_id, **kw):
    body = {"media_id": media_id, "device": "cpu"}
    body.update(kw)
    return c.post("/api/jobs", json=body)


def _finished_job(c, media_id) -> str:
    """A completed job row with one registered output file."""
    jid = _post_job(c, media_id).json()["id"]
    c.app.state.store.finish(jid, "completed")
    return jid


def _add_output(c, jid, tmp_path, name="out.mp3", data=b"0123456789"):
    payload = tmp_path / name
    payload.write_bytes(data)
    c.app.state.store.add_file(
        jid, OutputFile(name, "audio", payload, len(data))
    )
    return payload


def _media_row(db_path: Path, media_id: str = "m1") -> None:
    con = connect(db_path)
    try:
        con.execute(
            "INSERT OR IGNORE INTO media (id, filename, stored_path,"
            " size_bytes, probe_json, created_at) VALUES (?,?,?,?,?,?)",
            (media_id, "x.wav", "/x.wav", 1, "{}", "2026-11-15T00:00:00"),
        )
        con.commit()
    finally:
        con.close()


def _run(media_path, tmp_path, ops=None, output=None, preview=None):
    p = probe(media_path)
    events = []
    outs = run_pipeline(
        media_path=media_path,
        probe=p,
        audio_stream_index=p.audio[0].index,
        ops=ops or [],
        output=output or OutputSpec(),
        job_root=tmp_path / "job",
        on_progress=events.append,
        should_cancel=lambda: False,
        preview=preview,
        device="cpu",
    )
    return outs, events


# ---- item 3: files on list rows + SSE ----------------------------------------


def test_list_jobs_rows_carry_files(tmp_path):
    c = _client(tmp_path)
    mid = _upload(c, ARABIC_WAV)
    jid = _finished_job(c, mid)
    _add_output(c, jid, tmp_path, name="نتيجة.mp3", data=b"ABCD")

    rows = c.get("/api/jobs").json()["jobs"]
    row = next(r for r in rows if r["id"] == jid)
    assert row["files"] == [
        {
            "name": "نتيجة.mp3",
            "kind": "audio",
            "size_bytes": 4,
            "saved_path": None,
        }
    ]


def test_list_jobs_rows_files_empty(tmp_path):
    c = _client(tmp_path)
    mid = _upload(c, ARABIC_WAV)
    jid = _post_job(c, mid).json()["id"]
    rows = c.get("/api/jobs").json()["jobs"]
    row = next(r for r in rows if r["id"] == jid)
    assert row["files"] == []


def test_sse_state_event_carries_files(tmp_path):
    """R1 added files to SSE; every state event must keep carrying them so
    a reconnect sees current outputs."""
    c = _client(tmp_path)
    mid = _upload(c, ARABIC_WAV)
    jid = _finished_job(c, mid)
    _add_output(c, jid, tmp_path)

    got = []

    def _read():
        with c.stream("GET", f"/api/jobs/{jid}/events") as r:
            it = r.iter_lines()
            assert next(it) == "event: state"
            got.append(json.loads(next(it).split(":", 1)[1].strip()))

    t = threading.Thread(target=_read, daemon=True)
    t.start()
    t.join(10)
    assert not t.is_alive()
    assert got[0]["files"][0]["name"] == "out.mp3"


# ---- item 1: processed peaks ---------------------------------------------------


def test_runner_writes_processed_peaks(tmp_path):
    """After encode, output/peaks.json holds peaks of the final wav."""
    outs, _ = _run(ARABIC_WAV, tmp_path)
    peaks_path = tmp_path / "job" / "output" / "peaks.json"
    assert peaks_path.is_file()
    peaks = json.loads(peaks_path.read_text(encoding="utf-8"))
    assert peaks["sample_rate"] == 48000
    assert peaks["channels"] >= 1
    assert peaks["duration_s"] == pytest.approx(
        __import__("soundfile").info(outs[0].path).frames / 48000, abs=0.05
    )
    assert peaks["per_second"] > 0
    # one [min, max] pair per channel per bucket
    first = peaks["data"][0][0]
    assert len(first) == 2 and first[0] <= first[1]


def test_runner_peaks_inside_video_job_too(tmp_path):
    _, events = _run(CLIP, tmp_path)
    assert (tmp_path / "job" / "output" / "peaks.json").is_file()


def test_job_peaks_endpoint_404_until_completed(tmp_path):
    c = _client(tmp_path)
    mid = _upload(c, ARABIC_WAV)
    jid = _post_job(c, mid).json()["id"]  # queued, not completed
    assert c.get(f"/api/jobs/{jid}/peaks").status_code == 404
    assert c.get("/api/jobs/nope/peaks").status_code == 404


def test_job_peaks_endpoint_serves_file(tmp_path):
    c = _client(tmp_path)
    mid = _upload(c, ARABIC_WAV)
    jid = _finished_job(c, mid)
    outdir = tmp_path / "jobs" / jid / "output"
    outdir.mkdir(parents=True, exist_ok=True)
    peaks = {
        "sample_rate": 48000, "channels": 2, "duration_s": 1.0,
        "per_second": 50, "data": [[[-1, 1]], [[-1, 1]]],
    }
    (outdir / "peaks.json").write_text(
        json.dumps(peaks), encoding="utf-8"
    )
    r = c.get(f"/api/jobs/{jid}/peaks")
    assert r.status_code == 200
    assert r.json()["data"] == peaks["data"]


# ---- item 2: HTTP Range on job outputs -----------------------------------------


def test_job_file_download_supports_range(tmp_path):
    c = _client(tmp_path)
    mid = _upload(c, ARABIC_WAV)
    jid = _finished_job(c, mid)
    _add_output(c, jid, tmp_path, name="out.mp3", data=b"0123456789")

    r = c.get(
        f"/api/jobs/{jid}/files/out.mp3",
        headers={"Range": "bytes=2-5"},
    )
    assert r.status_code == 206
    assert r.content == b"2345"
    assert r.headers["content-range"] == "bytes 2-5/10"
    assert r.headers["accept-ranges"] == "bytes"
    # UTF-8 Content-Disposition survives the range path
    assert "attachment" in r.headers["content-disposition"]
    assert "out.mp3" in r.headers["content-disposition"]


def test_job_file_download_suffix_range_and_416(tmp_path):
    c = _client(tmp_path)
    mid = _upload(c, ARABIC_WAV)
    jid = _finished_job(c, mid)
    _add_output(c, jid, tmp_path)

    r = c.get(
        f"/api/jobs/{jid}/files/out.mp3", headers={"Range": "bytes=-4"}
    )
    assert r.status_code == 206
    assert r.content == b"6789"
    r = c.get(
        f"/api/jobs/{jid}/files/out.mp3", headers={"Range": "bytes=99-"}
    )
    assert r.status_code == 416


def test_job_file_download_full_request_has_accept_ranges(tmp_path):
    c = _client(tmp_path)
    mid = _upload(c, ARABIC_WAV)
    jid = _finished_job(c, mid)
    _add_output(c, jid, tmp_path)

    r = c.get(f"/api/jobs/{jid}/files/out.mp3")
    assert r.status_code == 200
    assert r.headers["accept-ranges"] == "bytes"
    assert r.headers["content-type"].startswith("audio/mpeg")
    assert "UTF-8''" in r.headers["content-disposition"]


def test_job_file_download_range_keeps_utf8_disposition(tmp_path):
    c = _client(tmp_path)
    mid = _upload(c, ARABIC_WAV)
    jid = _finished_job(c, mid)
    _add_output(c, jid, tmp_path, name="نتيجة.mp3", data=b"0123456789")
    r = c.get(
        "/api/jobs/" + jid + "/files/" + quote("نتيجة.mp3"),
        headers={"Range": "bytes=0-1"},
    )
    assert r.status_code == 206
    cd = r.headers["content-disposition"]
    assert "filename*=UTF-8''" in cd
    assert quote("نتيجة.mp3") in cd


# ---- item 4: stage_id -----------------------------------------------------------


def test_progress_stage_id_persisted(tmp_path):
    db = tmp_path / "app.db"
    init_db(db)
    _media_row(db)
    store = JobStore(db)
    job = store.create("m1", "full", [], {}, None, "cpu", 0)
    store.update_progress(
        job.id,
        Progress(
            stage="Reading file", stage_id="decode", stage_idx=0,
            total_stages=2, stage_pct=0.5, overall_pct=0.25,
        ),
    )
    got = store.get(job.id)
    assert got.stage == "Reading file"
    assert got.stage_id == "decode"


def test_runner_emits_stage_ids(tmp_path):
    _, events = _run(
        CLIP, tmp_path, ops=[("highpass", {"freq": 90})]
    )
    ids = [e.stage_id for e in events]
    assert "decode" in ids
    assert "highpass" in ids  # op stages use the op id
    assert "encode" in ids
    assert "video" in ids


def test_job_payload_carries_stage_id(tmp_path):
    c = _client(tmp_path)
    mid = _upload(c, ARABIC_WAV)
    jid = _post_job(c, mid).json()["id"]
    c.app.state.store.update_progress(
        jid,
        Progress(
            stage="Saving audio", stage_id="encode", stage_idx=1,
            total_stages=2, stage_pct=0.5, overall_pct=0.75,
        ),
    )
    body = c.get(f"/api/jobs/{jid}").json()
    assert body["stage_id"] == "encode"


# ---- item 5: eta_sec ------------------------------------------------------------


def test_progress_eta_sec_persisted_and_cleared_on_finish(tmp_path):
    db = tmp_path / "app.db"
    init_db(db)
    _media_row(db)
    store = JobStore(db)
    job = store.create("m1", "full", [], {}, None, "cpu", 0)
    assert store.get(job.id).eta_sec is None  # queued: unknown
    store.update_progress(
        job.id,
        Progress(
            stage="Cut silence", stage_id="silence", stage_idx=1,
            total_stages=3, stage_pct=0.5, overall_pct=0.5, eta_sec=42.0,
        ),
    )
    assert store.get(job.id).eta_sec == 42.0
    store.finish(job.id, "completed")
    assert store.get(job.id).eta_sec is None  # no stale ETA after done


def test_runner_emits_eta_while_running(tmp_path):
    _, events = _run(ARABIC_WAV, tmp_path)
    running = [e for e in events if e.stage_id != "done"]
    assert running, "no stage events"
    for e in running:
        assert e.eta_sec is not None, e
        assert e.eta_sec >= 0.0


def test_eta_remaining_sums_and_refines():
    from backend.pipeline.estimates import eta_remaining

    # ests [10, 20, 30]: at stage 0 half-done → 5 + 20 + 30 = 55
    assert eta_remaining([10.0, 20.0, 30.0], 0, 0.5, {}) == 55.0
    # stage 1 running, stage 0 measured 20 s vs est 10 → ratio 2 →
    # remaining (20*0.5 + 30) * 2 = 80
    assert eta_remaining(
        [10.0, 20.0, 30.0], 1, 0.5, {0: 20.0}
    ) == pytest.approx(80.0)
    # last stage: only its remaining fraction counts
    assert eta_remaining([10.0, 20.0], 1, 0.25, {}) == 15.0
    # unknown progress on the current stage counts it in full
    assert eta_remaining([10.0, 20.0], 1, None, {}) == 20.0


def test_eta_remaining_unknown_cases():
    from backend.pipeline.estimates import eta_remaining

    assert eta_remaining([], 0, 0.5, {}) is None
    assert eta_remaining([10.0], 5, 0.5, {}) == 0.0  # past end → done


def test_stage_estimates_match_runner_stage_list():
    from backend.pipeline.estimates import stage_estimates

    ops = [("trim", {"start_s": 1}), ("loudness", {})]
    ests = stage_estimates(60.0, ops, "cpu", want_video=True)
    # decode + 2 ops + encode + video
    assert len(ests) == 5
    assert all(s >= 0 for s in ests)
    no_video = stage_estimates(60.0, ops, "cpu", want_video=False)
    assert len(no_video) == 4
