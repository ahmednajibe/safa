"""M3a Task 4 + M3b Task 0 tests: media upload/probe/peaks/playback API.

Uploads are raw ``application/octet-stream`` bodies with the original
filename percent-encoded in the ``X-Filename`` header (M3b Task 0).
"""
import asyncio
import shutil
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

import backend.api.media as media_api
from backend.core.ffmpeg import find_ffmpeg
from backend.core.proc import run
from backend.core.settings import Settings
from backend.db import connect
from backend.main import create_app
from tests.client import make_client

GENERATED = Path(__file__).parent / "fixtures" / "generated"
CLIP = GENERATED / "clip.mp4"
ARABIC_WAV = GENERATED / "تسجيل اختبار 1.wav"


def _client(tmp_path) -> TestClient:
    return make_client(
        create_app(Settings(data_dir=tmp_path, db_path=tmp_path / "app.db"))
    )


def _upload(client, path: Path, name: str | None = None):
    return client.post(
        "/api/media",
        content=path.read_bytes(),
        headers={
            "X-Filename": quote(name or path.name),
            "Content-Type": "application/octet-stream",
        },
    )


def _media_root(tmp_path: Path) -> Path:
    return tmp_path / "media"


# ---- upload -----------------------------------------------------------------


def test_upload_clip_mp4(tmp_path):
    c = _client(tmp_path)
    r = _upload(c, CLIP)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["filename"] == "clip.mp4"
    assert body["size_bytes"] == CLIP.stat().st_size
    assert body["probe"]["has_video"] is True
    assert body["probe"]["video"][0]["codec"] == "h264"
    assert body["lufs"] is None
    mdir = _media_root(tmp_path) / body["media_id"]
    assert (mdir / "original.mp4").is_file()  # ascii on-disk name


def test_upload_arabic_name_preserved(tmp_path, real_dir):
    """Review Focus 1: Arabic + spaces display name kept exactly."""
    src = real_dir / "Recording (2).m4a"
    arabic_copy = tmp_path / "تسجيل عربي 1.m4a"
    shutil.copyfile(src, arabic_copy)
    c = _client(tmp_path)
    r = _upload(c, arabic_copy)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["filename"] == "تسجيل عربي 1.m4a"
    mdir = _media_root(tmp_path) / body["media_id"]
    assert (mdir / "original.m4a").is_file()
    # and the probe duration matches the real recording
    assert abs(body["probe"]["duration_s"] - 19.2) < 0.1


def test_upload_arabic_wav_fixture(tmp_path):
    c = _client(tmp_path)
    r = _upload(c, ARABIC_WAV)
    assert r.status_code == 201, r.text
    assert r.json()["filename"] == "تسجيل اختبار 1.wav"


def test_upload_txt_415(tmp_path):
    c = _client(tmp_path)
    bad = tmp_path / "notes.txt"
    bad.write_text("hello")
    r = _upload(c, bad)
    assert r.status_code == 415
    assert r.json()["code"] == "unsupported_type"
    assert not list(_media_root(tmp_path).iterdir()) if _media_root(tmp_path).exists() else True


def test_upload_no_audio_mp4_422(tmp_path):
    """Review Focus 2: video without audio → 422, nothing left on disk."""
    noaudio = tmp_path / "noaudio.mp4"
    run([
        find_ffmpeg(), "-hide_banner", "-y",
        "-f", "lavfi", "-i", "testsrc=size=320x240:duration=1:rate=10",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", noaudio,
    ], timeout=60)
    c = _client(tmp_path)
    r = _upload(c, noaudio)
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "no_audio"
    assert "no audio track" in body["message"]
    root = _media_root(tmp_path)
    assert not root.exists() or not any(root.iterdir())


def test_upload_garbage_422(tmp_path):
    c = _client(tmp_path)
    bad = tmp_path / "fake.wav"
    bad.write_bytes(b"not audio at all" * 100)
    r = _upload(c, bad)
    assert r.status_code == 422
    assert r.json()["code"] == "unreadable"


def test_upload_disk_full_507(tmp_path, monkeypatch):
    """Review Focus 3: nearly-full disk → 507, upload not started."""
    monkeypatch.setattr(media_api, "free_space_bytes", lambda p: 1)
    c = _client(tmp_path)
    r = _upload(c, CLIP)
    assert r.status_code == 507
    body = r.json()
    assert body["code"] == "insufficient_storage"
    assert "GB" in body["message"]
    root = _media_root(tmp_path)
    assert not root.exists() or not any(root.iterdir())


def test_upload_does_not_use_system_temp(tmp_path, monkeypatch):
    """Task 0.1: octet-stream upload never spools the body through %TEMP%
    (multipart parsing is what created the temp copy on C:)."""

    def _no_spool(*a, **k):
        raise AssertionError("multipart spooling touched tempfile")

    monkeypatch.setattr(tempfile, "SpooledTemporaryFile", _no_spool)
    c = _client(tmp_path)
    r = _upload(c, CLIP)
    assert r.status_code == 201, r.text
    mdir = _media_root(tmp_path) / r.json()["media_id"]
    assert (mdir / "original.mp4").stat().st_size == CLIP.stat().st_size


def test_upload_filename_header_arabic(tmp_path, real_dir):
    """Task 0.1: X-Filename is percent-encoded UTF-8; decoded name kept."""
    raw_name = "تسجيل بلس 100%.m4a"  # Arabic + spaces + a literal '%'
    c = _client(tmp_path)
    r = c.post(
        "/api/media",
        content=(real_dir / "Recording (2).m4a").read_bytes(),
        headers={
            "X-Filename": quote(raw_name),
            "Content-Type": "application/octet-stream",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["filename"] == raw_name
    mdir = _media_root(tmp_path) / body["media_id"]
    assert (mdir / "original.m4a").is_file()


def test_upload_blocking_calls_run_off_loop(tmp_path, monkeypatch):
    """Task 0.2: probe() and disk checks run in the threadpool, not on the
    event loop (``asyncio.get_running_loop`` fails off the loop)."""
    seen: dict[str, str] = {}

    def _spy(name, fn):
        def inner(*a, **k):
            try:
                asyncio.get_running_loop()
                seen[name] = "event-loop"
            except RuntimeError:
                seen[name] = "off-loop"
            return fn(*a, **k)

        return inner

    monkeypatch.setattr(
        media_api, "free_space_bytes",
        _spy("disk", media_api.free_space_bytes),
    )
    monkeypatch.setattr(
        media_api, "probe", _spy("probe", media_api.probe)
    )
    c = _client(tmp_path)
    r = _upload(c, CLIP)
    assert r.status_code == 201, r.text
    assert seen.get("disk") == "off-loop"
    assert seen.get("probe") == "off-loop"


def test_prep_failure_surfaces(tmp_path, monkeypatch):
    """Task 0.3: a failing prep is visible — ready.error + 500 prep_failed
    + prep.log — instead of /peaks answering 202 forever."""

    def _boom(*a, **k):
        raise RuntimeError("decode exploded")

    monkeypatch.setattr(media_api, "decode_to_wav", _boom)
    c = _client(tmp_path)
    r = _upload(c, CLIP)
    assert r.status_code == 201, r.text
    mid = r.json()["media_id"]
    deadline = time.time() + 15
    err_val = None
    while time.time() < deadline:
        info = c.get(f"/api/media/{mid}").json()
        err_val = info["ready"].get("error")
        if err_val:
            break
        time.sleep(0.2)
    assert err_val, "prep error never surfaced in ready.error"
    p = c.get(f"/api/media/{mid}/peaks")
    assert p.status_code == 500
    assert p.json()["code"] == "prep_failed"
    assert "couldn't read the audio" in p.json()["message"]
    assert (_media_root(tmp_path) / mid / "prep.log").is_file()


# ---- media info / peaks / playback / file -----------------------------------


def _upload_and_wait(c, path: Path, timeout: float = 20.0) -> str:
    r = _upload(c, path)
    assert r.status_code == 201, r.text
    mid = r.json()["media_id"]
    deadline = time.time() + timeout
    while time.time() < deadline:
        p = c.get(f"/api/media/{mid}/peaks")
        if p.status_code == 200:
            return mid
        assert p.status_code == 202 and p.json()["code"] == "processing"
        time.sleep(0.25)
    raise AssertionError("peaks never became ready")


def test_peaks_become_ready(tmp_path):
    c = _client(tmp_path)
    mid = _upload_and_wait(c, CLIP)
    r = c.get(f"/api/media/{mid}/peaks")
    body = r.json()
    assert body["sample_rate"] == 48000
    assert len(body["data"]) == 2  # stereo
    # media info reports readiness
    info = c.get(f"/api/media/{mid}").json()
    assert info["ready"]["peaks"] is True
    assert info["ready"]["playback"] is True
    assert info["lufs"] is not None


def test_peaks_other_stream_on_demand(tmp_path):
    """Review Focus 4: peaks honour the stream param."""
    multi = tmp_path / "multi.mkv"
    run([
        find_ffmpeg(), "-hide_banner", "-y",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-f", "lavfi", "-i", "sine=frequency=880:duration=2",
        "-f", "lavfi", "-i", "testsrc=size=320x240:duration=2:rate=15",
        "-map", "0:a", "-map", "1:a", "-map", "2:v",
        "-c:a", "aac", "-c:v", "libx264", "-shortest", multi,
    ], timeout=60)
    c = _client(tmp_path)
    mid = _upload_and_wait(c, multi)
    r = c.get(f"/api/media/{mid}/peaks", params={"stream": 1})
    assert r.status_code == 200, r.text
    assert r.json()["channels"] >= 1
    assert (_media_root(tmp_path) / mid / "peaks_1.json").exists()
    bad = c.get(f"/api/media/{mid}/peaks", params={"stream": 99})
    assert bad.status_code == 422


def test_playback_and_file_range(tmp_path):
    """Review Focus 5: HTTP Range for in-browser seeking."""
    c = _client(tmp_path)
    mid = _upload_and_wait(c, CLIP)
    pb = c.get(f"/api/media/{mid}/playback")
    assert pb.status_code == 200
    assert len(pb.content) > 1000

    full_size = CLIP.stat().st_size
    r = c.get(f"/api/media/{mid}/file", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206
    assert len(r.content) == 100
    assert r.headers["content-range"] == f"bytes 0-99/{full_size}"
    assert r.headers["accept-ranges"] == "bytes"
    # suffix range
    r2 = c.get(f"/api/media/{mid}/file", headers={"Range": "bytes=-50"})
    assert r2.status_code == 206 and len(r2.content) == 50
    # full GET still works and reports ranges
    r3 = c.get(f"/api/media/{mid}/file")
    assert r3.status_code == 200
    assert r3.headers["accept-ranges"] == "bytes"
    # out-of-range
    r4 = c.get(
        f"/api/media/{mid}/file",
        headers={"Range": f"bytes={full_size + 100}-"},
    )
    assert r4.status_code == 416


def test_media_not_found(tmp_path):
    c = _client(tmp_path)
    assert c.get("/api/media/nope").status_code == 404
    assert c.get("/api/media/nope/peaks").status_code == 404
    assert c.get("/api/media/nope/playback").status_code == 404
    assert c.get("/api/media/nope/file").status_code == 404
    assert c.delete("/api/media/nope").status_code == 404


def test_delete_removes_files_and_row(tmp_path):
    c = _client(tmp_path)
    mid = _upload_and_wait(c, CLIP)
    mdir = _media_root(tmp_path) / mid
    assert mdir.exists()
    r = c.delete(f"/api/media/{mid}")
    assert r.status_code == 204
    assert not mdir.exists()
    assert c.get(f"/api/media/{mid}").status_code == 404
    con = connect(tmp_path / "app.db")
    try:
        assert con.execute(
            "SELECT COUNT(*) FROM media WHERE id=?", (mid,)
        ).fetchone()[0] == 0
    finally:
        con.close()


# ---- R1 A1: Host / Origin guard ---------------------------------------------


def test_host_header_must_be_loopback(tmp_path):
    """R1 A1: DNS-rebinding Hosts get 400 before the route runs."""
    c = _client(tmp_path)
    for bad_host in (
        "evil.example.com", "127.0.0.1:9999", "localhost.evil.com",
    ):
        r = c.get("/api/health", headers={"Host": bad_host})
        assert r.status_code == 400, bad_host
        assert r.json()["code"] == "bad_host"
    for good in ("127.0.0.1:8790", "localhost:8790"):
        assert c.get("/api/health", headers={"Host": good}).status_code == 200


def test_cross_origin_mutation_rejected(tmp_path):
    """R1 A1: foreign Origin/Referer or cross-site fetch → 403 bad_origin;
    same-origin and header-less (curl/TestClient) requests pass."""
    c = _client(tmp_path)
    mid = _upload_and_wait(c, CLIP)
    foreign = {"Origin": "https://evil.example.com"}
    for r in (
        c.delete(f"/api/media/{mid}", headers=foreign),
        c.post("/api/jobs", json={"media_id": mid}, headers=foreign),
        c.post(f"/api/jobs/{'0' * 32}/cancel", headers=foreign),
        c.post(
            "/api/media",
            content=CLIP.read_bytes(),
            headers={**foreign, "X-Filename": "clip.mp4"},
        ),
    ):
        assert r.status_code == 403, r.text
        assert r.json()["code"] == "bad_origin"
    r = c.delete(
        f"/api/media/{mid}",
        headers={"Referer": "https://evil.example.com/page"},
    )
    assert r.status_code == 403 and r.json()["code"] == "bad_origin"
    r = c.delete(
        f"/api/media/{mid}", headers={"Sec-Fetch-Site": "cross-site"}
    )
    assert r.status_code == 403 and r.json()["code"] == "bad_origin"
    # same-origin → allowed through to the route
    r = c.delete(
        f"/api/media/{mid}", headers={"Origin": "http://localhost:8790"}
    )
    assert r.status_code == 204, r.text
    # no Origin/Referer at all (curl, tests) → allowed with a valid Host
    mid2 = _upload_and_wait(c, CLIP)
    assert c.delete(f"/api/media/{mid2}").status_code == 204


# ---- R1 A2: honest streaming limits ------------------------------------------


def test_upload_lying_content_length_413(tmp_path):
    """R1 A2: declared Content-Length < actual body must not bypass the
    size checks — what was written is what counts."""
    c = _client(tmp_path)
    r = c.post(
        "/api/media",
        content=CLIP.read_bytes(),
        headers={
            "X-Filename": "clip.mp4",
            "Content-Type": "application/octet-stream",
            "Content-Length": "10",
        },
    )
    assert r.status_code == 413, r.text
    assert r.json()["code"] == "too_large"
    root = _media_root(tmp_path)
    assert not root.exists() or not any(root.iterdir())


def test_upload_rechecks_disk_space_while_streaming(tmp_path, monkeypatch):
    """R1 A2: disk filling mid-stream → 507 even though the initial
    Content-Length-based check passed (re-check every 256 MB)."""
    calls = {"n": 0}
    real_free = media_api.free_space_bytes

    def _free(path):
        calls["n"] += 1
        return real_free(path) if calls["n"] == 1 else 0

    monkeypatch.setattr(media_api, "free_space_bytes", _free)
    monkeypatch.setattr(media_api, "_SPACE_CHECK_EVERY", 1)
    c = _client(tmp_path)
    r = _upload(c, CLIP)
    assert r.status_code == 507
    assert r.json()["code"] == "insufficient_storage"
    root = _media_root(tmp_path)
    assert not root.exists() or not any(root.iterdir())


def test_upload_still_enforces_max_upload_mb(tmp_path):
    """max_upload_mb applies to streamed bytes, not the declared length."""
    settings = Settings(
        data_dir=tmp_path, db_path=tmp_path / "app.db", max_upload_mb=0
    )
    c = make_client(create_app(settings))
    r = _upload(c, CLIP)
    assert r.status_code == 413
    assert r.json()["code"] == "too_large"


# ---- R1 A3: strict id validation ----------------------------------------------


@pytest.mark.parametrize(
    "bad",
    ["CON", "con", "A" * 32, "g" * 32, "1" * 31, "a" * 33, "x" * 32],
)
def test_media_id_must_be_32_lower_hex(tmp_path, bad):
    """R1 A3: anything but 32 lowercase hex gets 404 before filesystem use
    (covers reserved names, uppercase, wrong length, non-hex)."""
    c = _client(tmp_path)
    assert c.get(f"/api/media/{bad}").status_code == 404
    assert c.get(f"/api/media/{bad}/peaks").status_code == 404
    assert c.get(f"/api/media/{bad}/playback").status_code == 404
    assert c.get(f"/api/media/{bad}/file").status_code == 404
    assert c.delete(f"/api/media/{bad}").status_code == 404


def test_media_id_url_encoded_traversal_404(tmp_path):
    """..%2F decodes to a slash — never reaches media_dir as one id."""
    c = _client(tmp_path)
    r = c.get("/api/media/..%2F..")
    assert r.status_code == 404


@pytest.mark.parametrize(
    "bad", ["CON", "A" * 32, "z" * 32, "1" * 31],
)
def test_job_id_must_be_32_lower_hex(tmp_path, bad):
    c = _client(tmp_path)
    assert c.get(f"/api/jobs/{bad}").status_code == 404, bad
    assert c.post(f"/api/jobs/{bad}/cancel").status_code == 404, bad
    assert c.post(f"/api/jobs/{bad}/retry").status_code == 404, bad
    assert c.delete(f"/api/jobs/{bad}").status_code == 404, bad
    assert c.get(f"/api/jobs/{bad}/events").status_code == 404, bad
    assert c.get(f"/api/jobs/{bad}/timeline").status_code == 404, bad
    assert c.get(f"/api/jobs/{bad}/files/out.wav").status_code == 404, bad


def test_create_job_bad_media_id_404(tmp_path):
    c = _client(tmp_path)
    r = c.post("/api/jobs", json={"media_id": "A" * 32, "device": "cpu"})
    assert r.status_code == 404
    r = c.post(
        "/api/jobs", json={"media_id": "a" * 32, "device": "cpu"}
    )
    assert r.status_code == 404  # well-formed but unknown


# ---- R1 B1: delete with job history / during prep ------------------------------


def _new_job(store, mid: str):
    return store.create(
        media_id=mid,
        kind="full",
        ops=[],
        output={"audio_format": "wav", "keep_video": True},
        preview=None,
        device="cpu",
        audio_stream=0,
    )


def test_delete_media_with_completed_job_history(tmp_path):
    """R1 B1: media + terminal jobs + job_files + job dirs go in one
    transaction — no FK IntegrityError 500."""
    from backend.jobs.store import JobStore
    from backend.pipeline.types import OutputFile

    c = _client(tmp_path)
    mid = _upload_and_wait(c, CLIP)
    store = JobStore(tmp_path / "app.db")
    job = _new_job(store, mid)
    store.finish(job.id, "completed")
    outdir = tmp_path / "jobs" / job.id / "output"
    outdir.mkdir(parents=True)
    (outdir / "out.wav").write_bytes(b"x" * 10)
    store.add_file(
        job.id, OutputFile("out.wav", "audio", outdir / "out.wav", 10)
    )
    r = c.delete(f"/api/media/{mid}")
    assert r.status_code == 204, r.text
    con = connect(tmp_path / "app.db")
    try:
        assert con.execute(
            "SELECT COUNT(*) FROM media WHERE id=?", (mid,)
        ).fetchone()[0] == 0
        assert con.execute(
            "SELECT COUNT(*) FROM jobs WHERE media_id=?", (mid,)
        ).fetchone()[0] == 0
        assert con.execute(
            "SELECT COUNT(*) FROM job_files WHERE job_id=?", (job.id,)
        ).fetchone()[0] == 0
    finally:
        con.close()
    assert not (_media_root(tmp_path) / mid).exists()
    assert not (tmp_path / "jobs" / job.id).exists()


def test_delete_media_409_with_active_job(tmp_path):
    """R1 B1: queued/running jobs still protect the media (409 in_use)."""
    from backend.jobs.store import JobStore

    c = _client(tmp_path)
    mid = _upload_and_wait(c, CLIP)
    store = JobStore(tmp_path / "app.db")
    job = _new_job(store, mid)  # stays 'queued'
    r = c.delete(f"/api/media/{mid}")
    assert r.status_code == 409
    assert r.json()["code"] == "in_use"
    assert store.get(job.id) is not None
    assert (_media_root(tmp_path) / mid).exists()


def test_delete_during_prep_recreates_nothing(tmp_path, monkeypatch):
    """R1 B1: delete while _prepare runs → the prep thread is signalled and
    never writes into the deleted media dir again."""
    import threading as _t

    started = _t.Event()
    release = _t.Event()
    real_decode = media_api.decode_to_wav

    def _slow_decode(src, dst, stream_index, **kw):
        started.set()
        release.wait(20)
        return real_decode(src, dst, stream_index, **kw)

    monkeypatch.setattr(media_api, "decode_to_wav", _slow_decode)
    c = _client(tmp_path)
    r = _upload(c, CLIP)
    assert r.status_code == 201, r.text
    mid = r.json()["media_id"]
    assert started.wait(15), "prep never reached decode"
    mdir = _media_root(tmp_path) / mid
    r = c.delete(f"/api/media/{mid}")
    assert r.status_code == 204, r.text
    release.set()
    deadline = time.time() + 20
    while time.time() < deadline:
        with media_api._PREP_LOCK:
            done = mid not in media_api._PREP_THREADS
        if done:
            break
        time.sleep(0.1)
    assert not mdir.exists(), "prep wrote into the deleted media dir"


# ---- R1 B11: on-demand peaks locking / cleanup ---------------------------------


def test_peaks_other_stream_leaves_no_wav(tmp_path):
    """R1 B11: the temporary stream wav is unlinked after peaks land."""
    multi = tmp_path / "multi.mkv"
    run([
        find_ffmpeg(), "-hide_banner", "-y",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-f", "lavfi", "-i", "sine=frequency=880:duration=2",
        "-f", "lavfi", "-i", "testsrc=size=320x240:duration=2:rate=15",
        "-map", "0:a", "-map", "1:a", "-map", "2:v",
        "-c:a", "aac", "-c:v", "libx264", "-shortest", multi,
    ], timeout=60)
    c = _client(tmp_path)
    mid = _upload_and_wait(c, multi)
    r = c.get(f"/api/media/{mid}/peaks", params={"stream": 1})
    assert r.status_code == 200, r.text
    mdir = _media_root(tmp_path) / mid
    assert (mdir / "peaks_1.json").exists()
    assert not (mdir / "stream_1.wav").exists()
    assert not list(mdir.glob("*.tmp"))


def test_peaks_concurrent_same_stream_single_decode(tmp_path, monkeypatch):
    """R1 B11: concurrent requests for the same stream hold the per-
    (media, stream) lock — one decode, both callers get the same peaks."""
    multi = tmp_path / "multi.mkv"
    run([
        find_ffmpeg(), "-hide_banner", "-y",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-f", "lavfi", "-i", "sine=frequency=880:duration=2",
        "-f", "lavfi", "-i", "testsrc=size=320x240:duration=2:rate=15",
        "-map", "0:a", "-map", "1:a", "-map", "2:v",
        "-c:a", "aac", "-c:v", "libx264", "-shortest", multi,
    ], timeout=60)
    c = _client(tmp_path)
    mid = _upload_and_wait(c, multi)

    decodes = {"n": 0}
    lock = threading.Lock()
    real_decode = media_api.decode_to_wav
    release = threading.Event()

    def _counting_decode(src, dst, stream_index, **kw):
        with lock:
            decodes["n"] += 1
        release.wait(10)  # hold the lock so the second caller queues up
        return real_decode(src, dst, stream_index, **kw)

    monkeypatch.setattr(media_api, "decode_to_wav", _counting_decode)

    results: list[int] = []

    def _get():
        results.append(
            c.get(f"/api/media/{mid}/peaks", params={"stream": 1})
            .status_code
        )

    threads = [threading.Thread(target=_get) for _ in range(4)]
    for t in threads:
        t.start()
    time.sleep(0.3)
    release.set()
    for t in threads:
        t.join(20)
    assert results == [200] * 4
    assert decodes["n"] == 1
    mdir = _media_root(tmp_path) / mid
    assert (mdir / "peaks_1.json").exists()
    assert not (mdir / "stream_1.wav").exists()
