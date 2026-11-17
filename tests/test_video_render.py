"""M4b Task 3: video re-render from the timeline map via a filter script."""
import json
import subprocess
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

import backend.media.video_render as video_render
from backend.core.ffmpeg import find_ffmpeg, find_ffprobe, has_encoder
from backend.core.proc import ProcError
from backend.media.probe import probe
from backend.media.timeline import Segment, Timeline
from backend.media.video_render import render_video
from backend.pipeline.runner import run_pipeline
from backend.pipeline.types import CancelledError, OutputSpec

GENERATED = Path(__file__).parent / "fixtures" / "generated"
CLIP = GENERATED / "clip.mp4"
SR = 48000


def _sine_wav(path: Path, seconds: float) -> Path:
    t = np.arange(round(SR * seconds)) / SR
    sf.write(str(path), (0.3 * np.sin(2 * np.pi * 440 * t)).astype("float32"),
             SR, subtype="FLOAT")
    return path


def _gen_video(path: Path, duration: float = 10.0, rate: int = 25) -> Path:
    subprocess.run(
        [str(find_ffmpeg()), "-hide_banner", "-y",
         "-f", "lavfi", "-i",
         f"testsrc2=size=640x360:rate={rate}:duration={duration}",
         "-f", "lavfi", "-i",
         f"sine=frequency=440:duration={duration}",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(path)],
        check=True, capture_output=True,
    )
    return path


@pytest.fixture(scope="session")
def clip60(tmp_path_factory) -> Path:
    return _gen_video(
        tmp_path_factory.mktemp("v60") / "clip60.mp4", duration=60.0
    )


def _report():
    return lambda p, m=None: None


def test_trim_speed_durations_match_timeline(tmp_path):
    """Review Focus 1: keep(1,8)+speed1.5 on clip.mp4 → output video and
    audio durations equal within one frame and equal to out_duration."""
    tl = Timeline.identity(10.0).keep([(1.0, 8.0)]).with_speed(1.5)
    audio = _sine_wav(tmp_path / "a.wav", tl.out_duration)
    dst, enc = render_video(
        CLIP, probe(CLIP).video[0].index, audio, tl,
        tmp_path / "out" / "v", device="cpu", fps=25.0,
        report=_report(), should_cancel=lambda: False,
        script_path=tmp_path / "vf.txt",
    )
    assert enc == "libx264"
    assert dst.suffix == ".mp4"
    p = probe(dst)
    frame = 1.0 / 25.0
    # R1 B6: a speed change rescales timestamps, not the frame rate —
    # output stays CFR at the source fps (37.5 fps was the bug).
    assert p.video[0].fps == pytest.approx(25.0, abs=0.05)
    assert p.video[0].duration_s == pytest.approx(tl.out_duration,
                                                abs=frame)
    assert p.audio[0].duration_s == pytest.approx(tl.out_duration,
                                                abs=frame)
    assert abs(p.video[0].duration_s - p.audio[0].duration_s) <= frame


def test_slowdown_also_keeps_source_fps(tmp_path):
    """Speed < 1 duplicates frames at the source rate, not fps*speed."""
    tl = Timeline.identity(10.0).with_speed(0.75)
    audio = _sine_wav(tmp_path / "a.wav", tl.out_duration)
    dst, _ = render_video(
        CLIP, probe(CLIP).video[0].index, audio, tl,
        tmp_path / "out" / "v", device="cpu", fps=25.0,
        report=_report(), should_cancel=lambda: False,
        script_path=tmp_path / "vf.txt",
    )
    p = probe(dst)
    assert p.video[0].fps == pytest.approx(25.0, abs=0.05)
    assert p.video[0].duration_s == pytest.approx(
        tl.out_duration, abs=1.5 / 25.0
    )


def test_400_intervals_via_filter_script(tmp_path, clip60):
    """Review Focus 2: 400 keep-intervals render via -filter_complex_script
    (no command-line length blow-up)."""
    segs = []
    out = 0.0
    for i in range(400):
        a = i * 0.15
        b = a + 0.075
        segs.append(Segment(a, b, out))
        out += (b - a)
    tl = Timeline(60.0, tuple(segs)).snap(25.0)
    audio = _sine_wav(tmp_path / "a.wav", tl.out_duration)
    script = tmp_path / "video_filter.txt"
    dst, _ = render_video(
        clip60, probe(clip60).video[0].index, audio, tl,
        tmp_path / "out" / "v", device="cpu", fps=25.0,
        report=_report(), should_cancel=lambda: False,
        script_path=script,
    )
    assert script.is_file()
    assert "select" in script.read_text()
    p = probe(dst)
    # R1 weak test: two-frame tolerance (was abs=2.0 s) and the decoded
    # packet count must equal out_duration × fps within one frame —
    # a dropped/duplicated interval is invisible at ±2 s.
    assert p.video[0].duration_s == pytest.approx(
        tl.out_duration, abs=2.0 / 25.0
    )
    cp = subprocess.run(
        [str(find_ffprobe()), "-v", "error",
         "-select_streams", "v:0", "-show_entries", "packet=pts_time",
         "-print_format", "csv", str(dst)],
        capture_output=True, text=True, check=True,
    )
    n_packets = sum(1 for line in cp.stdout.splitlines() if "," in line)
    assert n_packets == pytest.approx(tl.out_duration * 25.0, abs=1)


def _gen_vfr(path: Path) -> Path:
    """VFR clip: first 125 frames at 25 fps, next 75 at 15 fps → avg 20."""
    subprocess.run(
        [str(find_ffmpeg()), "-hide_banner", "-y",
         "-f", "lavfi", "-i",
         "testsrc2=size=640x360:rate=25:duration=10",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=10",
         "-vf", "setpts='if(lt(N,125),N/(25*TB),5/TB+(N-125)/(15*TB))'",
         "-fps_mode", "vfr",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
         "-shortest", str(path)],
        check=True, capture_output=True,
    )
    return path


def test_vfr_input_stays_in_sync(tmp_path):
    """Review Focus 3: variable-frame-rate input renders CFR at the
    source's average fps and stays in sync with the audio."""
    vfr = _gen_vfr(tmp_path / "vfr.mkv")
    p_in = probe(vfr)
    fps_in = p_in.video[0].fps
    assert fps_in is not None
    tl = Timeline.identity(10.0).keep([(1.0, 9.0)]).snap(fps_in)
    audio = _sine_wav(tmp_path / "a.wav", tl.out_duration)
    dst, _ = render_video(
        vfr, p_in.video[0].index, audio, tl,
        tmp_path / "out" / "v", device="cpu", fps=fps_in,
        report=_report(), should_cancel=lambda: False,
        script_path=tmp_path / "vf.txt",
    )
    p = probe(dst)
    # CFR at the source's average fps: every inter-frame gap is 1/fps.
    cp = subprocess.run(
        [str(find_ffprobe()), "-v", "error",
         "-select_streams", "v:0", "-show_entries", "packet=pts_time",
         "-print_format", "csv", str(dst)],
        capture_output=True, text=True, check=True,
    )
    pts = sorted(
        float(line.split(",")[1]) for line in cp.stdout.splitlines()
        if "," in line
    )
    gaps = np.diff(pts)
    assert np.abs(gaps - 1.0 / fps_in).max() < 1e-3
    assert p.video[0].duration_s == pytest.approx(tl.out_duration,
                                                abs=1.5 / fps_in)
    assert abs(p.video[0].duration_s - p.audio[0].duration_s) <= 1.5 / fps_in


def test_gpu_job_uses_nvenc(tmp_path):
    # ffmpeg builds list h264_nvenc even with no NVIDIA GPU/driver (CI) —
    # the app then correctly falls back to libx264, so require a driver.
    import shutil
    if not has_encoder("h264_nvenc") or not shutil.which("nvidia-smi"):
        pytest.skip("h264_nvenc not usable (no NVIDIA driver)")
    tl = Timeline.identity(10.0).keep([(1.0, 5.0)])
    audio = _sine_wav(tmp_path / "a.wav", 4.0)
    _, enc = render_video(
        CLIP, 0, audio, tl, tmp_path / "v", device="gpu", fps=25.0,
        report=_report(), should_cancel=lambda: False,
        script_path=tmp_path / "vf.txt",
    )
    assert enc == "h264_nvenc"


def test_nvenc_failure_falls_back_to_libx264(tmp_path, monkeypatch):
    if not has_encoder("h264_nvenc"):
        pytest.skip("h264_nvenc not available")
    real_run_ffmpeg = video_render.run_ffmpeg
    calls = []

    def flaky(args, duration_s, on_pct, should_cancel, out_path=None):
        calls.append(1)
        if len(calls) == 1:
            raise ProcError(args, 1, "fake nvenc failure")
        return real_run_ffmpeg(args, duration_s, on_pct, should_cancel,
                               out_path=out_path)

    monkeypatch.setattr(video_render, "run_ffmpeg", flaky)
    tl = Timeline.identity(10.0).keep([(1.0, 5.0)])
    audio = _sine_wav(tmp_path / "a.wav", 4.0)
    dst, enc = render_video(
        CLIP, 0, audio, tl, tmp_path / "v", device="gpu", fps=25.0,
        report=_report(), should_cancel=lambda: False,
        script_path=tmp_path / "vf.txt",
    )
    assert enc == "libx264"
    assert len(calls) == 2
    assert probe(dst).video[0].codec == "h264"


def test_cancel_mid_render(tmp_path, clip60):
    tl = Timeline.identity(60.0).keep([(0.0, 30.0)])
    audio = _sine_wav(tmp_path / "a.wav", 30.0)
    with pytest.raises(CancelledError):
        render_video(
            clip60, 0, audio, tl, tmp_path / "v", device="cpu", fps=25.0,
            report=_report(), should_cancel=lambda: True,
            script_path=tmp_path / "vf.txt",
        )
    assert not (tmp_path / "v.mp4").exists()


# ---- runner integration ---------------------------------------------------


def test_runner_video_uses_render_when_timeline_edited(tmp_path):
    p = probe(CLIP)
    outs = run_pipeline(
        media_path=CLIP, probe=p, audio_stream_index=p.audio[0].index,
        ops=[("trim", {"start_s": 1.0, "end_s": 8.0}),
             ("speed", {"factor": 1.5})],
        output=OutputSpec(audio_format="wav", keep_video=True),
        job_root=tmp_path / "job",
        on_progress=lambda e: None, should_cancel=lambda: False,
        device="cpu",
    )
    video = next(o for o in outs if o.kind == "video")
    pv = probe(video.path)
    assert pv.video[0].duration_s == pytest.approx(7.0 / 1.5, abs=1 / 25)
    assert pv.audio[0].duration_s == pytest.approx(7.0 / 1.5, abs=1 / 25)
    tl = json.loads(
        (tmp_path / "job" / "output" / "timeline.json").read_text()
    )
    # decoded AAC runs ~5 ms past 10 s — the removed tail ends at the
    # decoded source duration, not the nominal 10.0
    assert tl["removed"][0] == [0.0, 1.0]
    assert tl["removed"][1][0] == 8.0
    assert tl["removed"][1][1] == pytest.approx(10.0, abs=0.02)


def test_runner_identity_remuxes_and_writes_timeline(tmp_path):
    p = probe(CLIP)
    outs = run_pipeline(
        media_path=CLIP, probe=p, audio_stream_index=p.audio[0].index,
        ops=[], output=OutputSpec(audio_format="wav", keep_video=True),
        job_root=tmp_path / "job",
        on_progress=lambda e: None, should_cancel=lambda: False,
        device="cpu",
    )
    video = next(o for o in outs if o.kind == "video")
    assert probe(video.path).video[0].codec == "h264"
    tl = json.loads(
        (tmp_path / "job" / "output" / "timeline.json").read_text()
    )
    assert tl["removed"] == []
    assert len(tl["segments"]) == 1
