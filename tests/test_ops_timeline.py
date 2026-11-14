"""M4b Task 2: trim + speed ops and the runner's timeline threading."""
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from backend.media.decode import decode_to_wav
from backend.media.probe import probe
from backend.media.timeline import Timeline
from backend.operations.base import OpContext
from backend.operations.impl import IMPLEMENTATIONS
from backend.pipeline.runner import run_pipeline
from backend.pipeline.types import OutputSpec

GENERATED = Path(__file__).parent / "fixtures" / "generated"
CLIP = GENERATED / "clip.mp4"
SR = 48000


def _write(path: Path, sig: np.ndarray) -> Path:
    sf.write(str(path), sig, SR, subtype="FLOAT")
    return path


def _sine(freq: float, seconds: float, amp: float = 0.4) -> np.ndarray:
    t = np.arange(int(SR * seconds)) / SR
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def _ctx(tmp_path: Path, in_wav: Path, fps=None, timeline=None) -> OpContext:
    info = sf.info(str(in_wav))
    return OpContext(
        work_dir=tmp_path,
        device="cpu",
        duration_s=info.frames / info.samplerate,
        sample_rate=info.samplerate,
        channels=info.channels,
        report=lambda p, m: None,
        should_cancel=lambda: False,
        fps=fps,
        timeline=timeline,
    )


def _frames(path: Path) -> int:
    return sf.info(str(path)).frames


def _dominant_freq(path: Path) -> float:
    x, sr = sf.read(str(path), dtype="float32", always_2d=True)
    sig = x[:, 0] * np.hanning(len(x))
    spec = np.abs(np.fft.rfft(sig))
    return float(np.argmax(spec) * sr / len(sig))


# ---- trim ---------------------------------------------------------------------


def test_trim_duration_exact(tmp_path):
    src = _write(tmp_path / "in.wav", _sine(440, 5.0))
    ctx = _ctx(tmp_path, src)
    res = IMPLEMENTATIONS["trim"](
        src, {"start_s": 1.0, "end_s": 3.0}, ctx
    )
    assert _frames(res.path) == 2 * SR
    assert res.timeline.kept_spans_src() == [(1.0, 3.0)]
    assert res.timeline.out_duration == pytest.approx(2.0)


def test_trim_open_end(tmp_path):
    src = _write(tmp_path / "in.wav", _sine(440, 5.0))
    res = IMPLEMENTATIONS["trim"](
        src, {"start_s": 1.5}, _ctx(tmp_path, src)
    )
    assert _frames(res.path) == _frames(src) - int(1.5 * SR)


def test_trim_invalid_raises_human_message(tmp_path):
    src = _write(tmp_path / "in.wav", _sine(440, 5.0))
    ctx = _ctx(tmp_path, src)
    with pytest.raises(ValueError, match="Trim start must be before"):
        IMPLEMENTATIONS["trim"](src, {"start_s": 3.0, "end_s": 3.0}, ctx)
    with pytest.raises(ValueError, match="Trim start must be before"):
        IMPLEMENTATIONS["trim"](src, {"start_s": 9.0}, ctx)


def test_trim_snaps_to_video_frame_grid(tmp_path):
    src = _write(tmp_path / "in.wav", _sine(440, 10.0))
    ctx = _ctx(tmp_path, src, fps=25.0)
    res = IMPLEMENTATIONS["trim"](
        src, {"start_s": 0.53, "end_s": 4.97}, ctx
    )
    # 0.53→0.52, 4.97→4.96 on the 25 fps grid (13 and 124 frames)
    assert res.timeline.kept_spans_src() == [(0.52, 4.96)]
    assert _frames(res.path) == round((4.96 - 0.52) * SR)


def test_trim_composes_on_existing_timeline(tmp_path):
    """A trim applied to an already-sped-up input maps through the parent
    timeline (input time == parent output time)."""
    src = _write(tmp_path / "in.wav", _sine(440, 4.0))  # = 8 s src at 2×
    parent = Timeline.identity(8.0).with_speed(2.0)
    ctx = _ctx(tmp_path, src, timeline=parent)
    res = IMPLEMENTATIONS["trim"](
        src, {"start_s": 1.0, "end_s": 3.0}, ctx
    )
    # out seconds 1..3 of the 2× map → source seconds 2..6
    assert res.timeline.kept_spans_src() == [(2.0, 6.0)]
    assert _frames(res.path) == 2 * SR


def test_trim_real_recording(real_dir, tmp_path):
    src = tmp_path / "real.wav"
    decode_to_wav(real_dir / "Recording.m4a", src, 0)
    n_in = _frames(src)
    res = IMPLEMENTATIONS["trim"](
        src, {"start_s": 2.0, "end_s": 12.0}, _ctx(tmp_path, src)
    )
    assert _frames(res.path) == 10 * SR
    assert res.timeline.out_duration == pytest.approx(10.0)
    assert n_in > 12 * SR


# ---- speed --------------------------------------------------------------------


def test_speed_15_length_exact_and_pitch(tmp_path):
    src = _write(tmp_path / "in.wav", _sine(440, 3.0))
    res = IMPLEMENTATIONS["speed"](
        src, {"factor": 1.5}, _ctx(tmp_path, src)
    )
    assert _frames(res.path) == round(_frames(src) / 1.5)
    assert _dominant_freq(res.path) == pytest.approx(440.0, abs=2.0)
    assert res.timeline.out_duration == pytest.approx(3.0 / 1.5)


def test_speed_075_length_exact_and_pitch(tmp_path):
    src = _write(tmp_path / "in.wav", _sine(440, 3.0))
    res = IMPLEMENTATIONS["speed"](
        src, {"factor": 0.75}, _ctx(tmp_path, src)
    )
    assert _frames(res.path) == round(_frames(src) / 0.75)
    assert _dominant_freq(res.path) == pytest.approx(440.0, abs=2.0)


def test_speed_1_passthrough(tmp_path):
    src = _write(tmp_path / "in.wav", _sine(440, 1.0))
    res = IMPLEMENTATIONS["speed"](
        src, {"factor": 1.0}, _ctx(tmp_path, src)
    )
    assert _frames(res.path) == _frames(src)


def test_ops_registered():
    for op in ("trim", "speed"):
        assert op in IMPLEMENTATIONS, f"{op} not registered"


# ---- runner threading -----------------------------------------------------------


def test_runner_threads_timeline_and_fps(tmp_path):
    """trim(1,8) + speed(1.5) on the 10 s 25 fps clip: audio ends at
    7/1.5 s and the final timeline reflects both ops."""
    p = probe(CLIP)
    outs = run_pipeline(
        media_path=CLIP,
        probe=p,
        audio_stream_index=p.audio[0].index,
        ops=[("trim", {"start_s": 1.0, "end_s": 8.0}),
             ("speed", {"factor": 1.5})],
        output=OutputSpec(audio_format="wav", keep_video=False),
        job_root=tmp_path / "job",
        on_progress=lambda e: None,
        should_cancel=lambda: False,
        device="cpu",
    )
    audio = next(o for o in outs if o.kind == "audio")
    n = sf.info(str(audio.path)).frames
    assert n == pytest.approx(round(7.0 / 1.5 * SR), abs=SR * 0.02)
