"""M3a Task 4 + M3b Task 0 tests: media upload/probe/peaks/playback API.

Uploads are raw ``application/octet-stream`` bodies with the original
filename percent-encoded in the ``X-Filename`` header (M3b Task 0).
"""
import asyncio
import shutil
import tempfile
import time
from pathlib import Path
from urllib.parse import quote

from fastapi.testclient import TestClient

import backend.api.media as media_api
from backend.core.ffmpeg import find_ffmpeg
from backend.core.proc import run
from backend.core.settings import Settings
from backend.db import connect
from backend.main import create_app

GENERATED = Path(__file__).parent / "fixtures" / "generated"
CLIP = GENERATED / "clip.mp4"
ARABIC_WAV = GENERATED / "تسجيل اختبار 1.wav"


def _client(tmp_path) -> TestClient:
    return TestClient(
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
