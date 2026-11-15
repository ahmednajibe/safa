"""M3b Task 4 tests: /api/jobs — submit, list, SSE, cancel, retry, delete,
file downloads. Flow tests use the lifespan (real supervisor + worker
subprocess); validation tests use a bare client (job stays queued)."""
import json
import threading
import time
from pathlib import Path
from urllib.parse import quote

import numpy as np
import psutil
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

import backend.api.jobs as jobs_api
from backend.core.capabilities import Capabilities
from backend.core.settings import Settings
from backend.main import create_app
from backend.pipeline.types import OutputFile

GENERATED = Path(__file__).parent / "fixtures" / "generated"
ARABIC_WAV = GENERATED / "تسجيل اختبار 1.wav"
SPEECH = GENERATED / "speech.wav"


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


def _caps_no_gpu():
    return Capabilities(
        python="3.11", gpu=None, cuda_dlls=False, ffmpeg={},
        modules={}, models={},
    )


def _post_job(c, media_id, **kw):
    body = {"media_id": media_id, "device": "cpu"}
    body.update(kw)
    return c.post("/api/jobs", json=body)


def _collect_sse(c, jid: str, timeout: float = 120):
    """Read SSE until 'event: done'; returns [(event, payload)]."""
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
                elif line.startswith(":"):
                    got.append(("ping", line))

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout)
    assert not t.is_alive(), "SSE stream never sent 'done'"
    return got


# ---- submission validation -----------------------------------------------------


def test_post_job_media_not_found(tmp_path):
    c = _client(tmp_path)
    r = _post_job(c, "nope")
    assert r.status_code == 404
    assert r.json()["code"] == "media_not_found"


def test_post_job_conversion_empty_ops(tmp_path):
    """Empty ops = pass-through conversion — allowed, job queued."""
    c = _client(tmp_path)
    mid = _upload(c, ARABIC_WAV)
    r = _post_job(
        c, mid, output={"audio_format": "flac", "keep_video": False}
    )
    assert r.status_code == 201, r.text
    job = r.json()
    assert job["state"] == "queued"
    assert job["queue_position"] == 1
    assert job["kind"] == "full"
    assert job["requested_device"] == "cpu"
    assert job["ops"] == []


def test_post_job_unknown_op_422(tmp_path):
    c = _client(tmp_path)
    mid = _upload(c, ARABIC_WAV)
    r = _post_job(c, mid, ops={"frobnicate": {}})
    assert r.status_code == 422
    assert "robnicate" in r.json()["message"]


def test_post_job_disabled_ops_reported(tmp_path):
    """Planned/conflicted ops are dropped and returned in 'disabled'."""
    c = _client(tmp_path)
    mid = _upload(c, ARABIC_WAV)
    r = _post_job(c, mid, ops={"enhance_ai": {}})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["disabled"].get("enhance_ai")
    assert body["ops"] == []


def test_post_job_karaoke_conflict_disables_denoise(tmp_path):
    c = _client(tmp_path)
    mid = _upload(c, ARABIC_WAV)
    r = _post_job(
        c, mid,
        ops={
            "separate": {"output": "instrumental"},
            "denoise": {},
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    # separate is implemented (M7), so the karaoke conflict fires and
    # denoise — a speech op — is disabled with ``separate`` as blocker.
    assert "separate" not in body["disabled"]
    denoise = body["disabled"]["denoise"]
    assert denoise["code"] == "karaoke_speech_op"
    assert denoise["blocker_op_id"] == "separate"
    assert [op["id"] for op in body["ops"]] == ["separate"]


def test_post_job_gpu_unavailable(tmp_path, monkeypatch):
    c = _client(tmp_path)
    mid = _upload(c, ARABIC_WAV)
    monkeypatch.setattr(
        jobs_api, "detect_capabilities", _caps_no_gpu
    )
    r = c.post(
        "/api/jobs",
        json={"media_id": mid, "device": "gpu", "ops": {}},
    )
    assert r.status_code == 422
    assert r.json()["code"] == "gpu_unavailable"
    # cpu still fine
    assert _post_job(c, mid).status_code == 201


def test_post_job_bad_device_and_output(tmp_path):
    c = _client(tmp_path)
    mid = _upload(c, ARABIC_WAV)
    r = c.post("/api/jobs", json={"media_id": mid, "device": "tpu"})
    assert r.status_code == 422
    r = _post_job(c, mid, output={"audio_format": "ogg"})
    assert r.status_code == 422


def test_post_job_preview_validation(tmp_path):
    c = _client(tmp_path)
    mid = _upload(c, ARABIC_WAV)
    # length must be 1..60 s
    for pv in ({"start_s": 0, "end_s": 0.5}, {"start_s": 0, "end_s": 90},
               {"start_s": 5, "end_s": 2}):
        r = _post_job(c, mid, preview=pv)
        assert r.status_code == 422, pv
    r = _post_job(c, mid, preview={"start_s": 0, "end_s": 5})
    assert r.status_code == 201
    assert r.json()["kind"] == "preview"
    assert r.json()["preview"] == {"start_s": 0.0, "end_s": 5.0}


def test_post_job_bad_audio_stream(tmp_path):
    c = _client(tmp_path)
    mid = _upload(c, ARABIC_WAV)
    r = _post_job(c, mid, audio_stream=99)
    assert r.status_code == 422
    assert r.json()["code"] == "no_such_stream"


# ---- list / get ------------------------------------------------------------------


def test_list_and_get_job(tmp_path):
    c = _client(tmp_path)
    mid = _upload(c, ARABIC_WAV)
    jid = _post_job(c, mid).json()["id"]
    r = c.get("/api/jobs")
    assert r.status_code == 200
    jobs = r.json()["jobs"]
    assert jobs[0]["id"] == jid
    assert jobs[0]["filename"] == "تسجيل اختبار 1.wav"
    r = c.get(f"/api/jobs/{jid}")
    assert r.status_code == 200
    body = r.json()
    assert body["queue_position"] == 1
    assert body["files"] == []
    assert c.get("/api/jobs/nope").status_code == 404


# ---- cancel / retry / delete -------------------------------------------------------


def test_cancel_queued_job(tmp_path):
    c = _client(tmp_path)
    mid = _upload(c, ARABIC_WAV)
    jid = _post_job(c, mid).json()["id"]
    r = c.post(f"/api/jobs/{jid}/cancel")
    assert r.status_code == 202
    assert c.app.state.store.get(jid).state == "cancelled"
    # terminal now → 409
    assert c.post(f"/api/jobs/{jid}/cancel").status_code == 409
    assert c.post("/api/jobs/nope/cancel").status_code == 404


def test_cancel_running_job_kills_worker(tmp_path):
    """R1 weak test #10 + B2: the cancel must exercise the RUNNING path —
    wait for ``running`` + a live worker pid, then /cancel. The API
    answers without waiting on the kill; the worker process dies and the
    job ends up ``cancelled``."""
    c = _client(tmp_path)
    src = tmp_path / "long_tone.wav"
    t = np.arange(60 * 48000) / 48000
    sf.write(
        str(src), (0.3 * np.sin(2 * np.pi * 220 * t)).astype("float32"),
        48000, subtype="FLOAT",
    )
    with c:  # lifespan → real supervisor + worker subprocess
        mid = _upload(c, src)
        jid = _post_job(c, mid).json()["id"]
        store = c.app.state.store
        pid = None
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            for j_id, p in store.running_pids():
                if j_id == jid:
                    pid = p
            if pid is not None:
                break
            assert store.get(jid).state != "completed", (
                "job finished before cancel could land"
            )
            time.sleep(0.05)
        assert pid is not None, (
            "job never reached 'running' with a live worker pid"
        )
        assert psutil.pid_exists(pid)
        r = c.post(f"/api/jobs/{jid}/cancel")
        assert r.status_code == 202
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if store.get(jid).state == "cancelled":
                break
            time.sleep(0.1)
        assert store.get(jid).state == "cancelled"
        assert not psutil.pid_exists(pid), (
            "worker process survived the cancel"
        )


def test_retry_only_from_terminal(tmp_path):
    c = _client(tmp_path)
    mid = _upload(c, ARABIC_WAV)
    store = c.app.state.store
    j = _post_job(c, mid).json()["id"]
    # queued → 409
    assert c.post(f"/api/jobs/{j}/retry").status_code == 409
    store.finish(j, "failed", error="boom")
    r = c.post(f"/api/jobs/{j}/retry")
    assert r.status_code == 201, r.text
    new = r.json()
    assert new["id"] != j
    assert new["retry_of"] == j
    assert new["state"] == "queued"
    # completed can't retry
    store.finish(new["id"], "completed")
    assert c.post(f"/api/jobs/{new['id']}/retry").status_code == 409


def test_delete_job(tmp_path):
    c = _client(tmp_path)
    mid = _upload(c, ARABIC_WAV)
    store = c.app.state.store
    j = _post_job(c, mid).json()["id"]
    # queued → 409
    assert c.delete(f"/api/jobs/{j}").status_code == 409
    store.finish(j, "completed")
    jdir = tmp_path / "jobs" / j
    jdir.mkdir(parents=True, exist_ok=True)
    (jdir / "x.txt").write_text("x")
    assert c.delete(f"/api/jobs/{j}").status_code == 204
    assert not jdir.exists()
    assert c.get(f"/api/jobs/{j}").status_code == 404


# ---- downloads -------------------------------------------------------------------


def test_files_download_and_traversal(tmp_path):
    c = _client(tmp_path)
    mid = _upload(c, ARABIC_WAV)
    store = c.app.state.store
    j = _post_job(c, mid).json()["id"]
    store.finish(j, "completed")
    payload = tmp_path / "out.mp3"
    payload.write_bytes(b"MP3DATA")
    store.add_file(j, OutputFile("out.mp3", "audio", payload, 7))
    r = c.get(f"/api/jobs/{j}/files/out.mp3")
    assert r.status_code == 200
    assert r.content == b"MP3DATA"
    assert "attachment" in r.headers["content-disposition"]
    # unregistered name / traversal → 404
    assert c.get(f"/api/jobs/{j}/files/other.mp3").status_code == 404
    assert c.get(
        f"/api/jobs/{j}/files/..%2F..%2Fapp.db"
    ).status_code == 404
    assert c.get("/api/jobs/nope/files/out.mp3").status_code == 404


def test_download_arabic_filename_header(tmp_path):
    c = _client(tmp_path)
    mid = _upload(c, ARABIC_WAV)
    store = c.app.state.store
    j = _post_job(c, mid).json()["id"]
    payload = tmp_path / "p.bin"
    payload.write_bytes(b"X")
    store.add_file(j, OutputFile("نتيجة.mp3", "audio", payload, 1))
    r = c.get(f"/api/jobs/{j}/files/" + quote("نتيجة.mp3"))
    assert r.status_code == 200
    cd = r.headers["content-disposition"]
    assert "filename*=UTF-8''" in cd
    assert quote("نتيجة.mp3") in cd


# ---- SSE ------------------------------------------------------------------------


def test_sse_first_event_is_full_state(tmp_path):
    """Review Focus 5: reconnect mid-job → first event is current state."""
    c = _client(tmp_path)
    mid = _upload(c, ARABIC_WAV)
    jid = _post_job(c, mid).json()["id"]

    for _ in range(2):  # connect, read one event, close, reconnect
        with c.stream("GET", f"/api/jobs/{jid}/events") as r:
            it = r.iter_lines()
            first = next(it)
            assert first == "event: state"
            data = next(it)
            assert data.startswith("data: ")
            payload = json.loads(data[6:])
            assert payload["id"] == jid
            assert payload["state"] == "queued"


def test_sse_unknown_job_404(tmp_path):
    c = _client(tmp_path)
    assert c.get("/api/jobs/nope/events").status_code == 404


def test_sse_open_close_60_streams_no_leak(tmp_path):
    """R1 B4: 60 open/close cycles on a live job's SSE stream must not
    pin the server — a normal GET still answers < 1 s. The old sync
    generator held a threadpool thread per open stream until the job
    ended (≈40 refreshes exhausted the pool)."""
    c = _client(tmp_path)
    mid = _upload(c, ARABIC_WAV)
    jid = _post_job(c, mid).json()["id"]  # stays queued (no lifespan)
    for _ in range(60):
        with c.stream("GET", f"/api/jobs/{jid}/events") as r:
            first = next(r.iter_lines())
            assert first == "event: state"
    t0 = time.monotonic()
    resp = c.get("/api/health")
    assert resp.status_code == 200
    assert time.monotonic() - t0 < 1.0


# ---- full flow on a real recording ------------------------------------------------


def test_full_flow_real_recording(tmp_path, real_dir):
    """Submit → SSE until done → download → bytes match the stored file."""
    c = _client(tmp_path)
    with c:  # lifespan: supervisor.start()
        mid = _upload(c, real_dir / "Recording.m4a")
        r = _post_job(
            c, mid, output={"audio_format": "mp3", "keep_video": False}
        )
        assert r.status_code == 201, r.text
        jid = r.json()["id"]

        events = _collect_sse(c, jid)
        assert events[0][0] == "state"
        assert events[0][1]["id"] == jid
        assert events[-1][0] == "done"
        final = events[-1][1]
        assert final["state"] == "completed"
        assert final["overall_pct"] == 1.0

        info = c.get(f"/api/jobs/{jid}").json()
        assert info["state"] == "completed"
        assert len(info["files"]) == 1
        name = info["files"][0]["name"]
        assert name.endswith(".mp3")

        dl = c.get(f"/api/jobs/{jid}/files/{name}")
        assert dl.status_code == 200
        stored = Path(
            c.app.state.store.files(jid)[0]["path"]
        ).read_bytes()
        assert dl.content == stored


def test_job_output_uses_display_filename(tmp_path, real_dir):
    """M4a Task 0 regression: output files must be named from the upload's
    display filename (media.filename), not the stored ``original.<ext>``
    path — ``تسجيل (3).m4a`` → ``تسجيل (3)_clean.flac``."""
    c = _client(tmp_path)
    with c:  # lifespan: real supervisor + worker subprocess
        mid = _upload(
            c, real_dir / "Recording (3).m4a", name="تسجيل (3).m4a"
        )
        r = _post_job(
            c, mid,
            output={"audio_format": "flac", "keep_video": False},
        )
        assert r.status_code == 201, r.text
        jid = r.json()["id"]

        events = _collect_sse(c, jid)
        assert events[-1][1]["state"] == "completed"

        info = c.get(f"/api/jobs/{jid}").json()
        names = [f["name"] for f in info["files"]]
        assert names == ["تسجيل (3)_clean.flac"]

        dl = c.get(f"/api/jobs/{jid}/files/" + quote("تسجيل (3)_clean.flac"))
        assert dl.status_code == 200
        cd = dl.headers["content-disposition"]
        assert "filename*=UTF-8''" in cd
        assert quote("تسجيل (3)_clean.flac") in cd


def test_preview_job_returns_audio_only(tmp_path):
    c = _client(tmp_path)
    with c:
        mid = _upload(c, SPEECH)
        r = _post_job(
            c, mid, preview={"start_s": 1.0, "end_s": 3.0},
            output={"audio_format": "wav", "keep_video": True},
        )
        assert r.status_code == 201, r.text
        jid = r.json()["id"]
        assert r.json()["kind"] == "preview"
        events = _collect_sse(c, jid)
        assert events[-1][1]["state"] == "completed"
        info = c.get(f"/api/jobs/{jid}").json()
        assert len(info["files"]) == 1
        f0 = info["files"][0]
        assert f0["kind"] == "audio" and "preview" in f0["name"]
