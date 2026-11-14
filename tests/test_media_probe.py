"""M3a Task 1 tests: proc wrapper, ffprobe probe, storage helpers."""
from pathlib import Path

import pytest

from backend.core.ffmpeg import find_ffmpeg, find_ffprobe
from backend.core.proc import ProcError, run
from backend.media.probe import ProbeError, probe
from backend.media.storage import (
    ALLOWED_EXTS,
    display_name,
    free_space_bytes,
    job_dir,
    media_dir,
    new_id,
)

GENERATED = Path(__file__).parent / "fixtures" / "generated"
CLIP = GENERATED / "clip.mp4"
ARABIC_NAME = "تسجيل اختبار 1.wav"


def test_run_returns_completed_process():
    cp = run([find_ffprobe(), "-version"])
    assert cp.returncode == 0
    assert "ffprobe version" in cp.stdout


def test_run_raises_proc_error_on_failure(tmp_path):
    with pytest.raises(ProcError) as exc:
        run([find_ffmpeg(), "-hide_banner", "-i", str(tmp_path / "nope.wav")])
    assert exc.value.returncode != 0
    assert exc.value.stderr_tail


def test_probe_clip_mp4():
    p = probe(CLIP)
    assert abs(p.duration_s - 10.0) < 0.1
    assert p.has_video
    assert len(p.video) == 1
    assert len(p.audio) == 1
    v = p.video[0]
    assert (v.codec, v.width, v.height) == ("h264", 640, 360)
    a = p.audio[0]
    assert a.codec == "aac"
    assert a.sample_rate == 48000
    assert a.channels == 2
    d = p.to_dict()
    assert d["has_video"] is True
    assert d["size_bytes"] == CLIP.stat().st_size


def test_probe_real_m4a(real_dir):
    p = probe(real_dir / "Recording (2).m4a")
    assert not p.has_video
    assert len(p.audio) == 1
    a = p.audio[0]
    assert a.codec == "aac"
    assert a.sample_rate == 48000
    assert a.channels == 2
    assert abs(p.duration_s - 19.2) < 0.1


def test_probe_multi_audio_mkv(tmp_path):
    """Review Focus 4: mkv with 2 audio streams — probe lists both."""
    dst = tmp_path / "multi.mkv"
    run([
        find_ffmpeg(), "-hide_banner", "-y",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-f", "lavfi", "-i", "sine=frequency=880:duration=2",
        "-f", "lavfi", "-i", "testsrc=size=320x240:duration=2:rate=15",
        "-map", "0:a", "-map", "1:a", "-map", "2:v",
        "-c:a", "aac", "-c:v", "libx264", "-shortest",
        dst,
    ], timeout=60)
    p = probe(dst)
    assert len(p.audio) == 2
    assert {a.index for a in p.audio} == {0, 1}
    assert p.has_video


def test_probe_garbage_raises(tmp_path):
    bad = tmp_path / "garbage.wav"
    bad.write_text("this is not media at all")
    with pytest.raises(ProbeError):
        probe(bad)


def test_display_name_keeps_arabic():
    assert display_name(ARABIC_NAME) == ARABIC_NAME


def test_display_name_strips_dirs_and_controls():
    assert display_name("C:\\Users\\me\\file.wav") == "file.wav"
    assert display_name("a/b/file.wav") == "file.wav"
    assert display_name("na\x00me\x1f.wav") == "name.wav"
    assert len(display_name("x" * 500 + ".wav")) <= 200


def test_storage_dirs_and_ids(tmp_path):
    mdir = media_dir("abc123", tmp_path)
    assert mdir == tmp_path / "media" / "abc123"
    jdir = job_dir("job9", tmp_path)
    assert (jdir / "work").is_dir() and (jdir / "output").is_dir()
    assert new_id() != new_id()
    assert "wav" in ALLOWED_EXTS and "mp4" in ALLOWED_EXTS
    assert "txt" not in ALLOWED_EXTS
    assert free_space_bytes(tmp_path) > 0
