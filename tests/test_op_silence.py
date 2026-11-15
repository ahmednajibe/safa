"""M5 Task 4: the ``silence`` op — detect → plan → snap → render.

Defaults remove every silence ≥ 0.3 s (SPEC §10); ``shorten`` leaves
``keep_pause_s`` per long pause. Output and returned timeline agree; on
video jobs the cut boundaries snap to the frame grid.
"""
import json
import shutil
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from backend.media.timeline import Timeline
from backend.operations.base import OpContext
from backend.operations.impl import IMPLEMENTATIONS

GENERATED = Path(__file__).parent / "fixtures" / "generated"
SPEECH_SIL = GENERATED / "speech_silences.wav"
SILENCE = GENERATED / "silence.wav"
SPEECH = GENERATED / "speech.wav"
SR = 48000


def _dur(path: Path) -> float:
    info = sf.info(str(path))
    return info.frames / info.samplerate


def _ctx(
    tmp_path: Path, in_wav: Path, fps=None, timeline=None,
    should_cancel=None,
) -> OpContext:
    return OpContext(
        work_dir=tmp_path,
        device="cpu",
        duration_s=_dur(in_wav),
        sample_rate=SR,
        channels=1,
        report=lambda *a: None,
        should_cancel=should_cancel or (lambda: False),
        fps=fps,
        timeline=timeline,
    )


def _run(in_wav: Path, tmp_path: Path, params=None, **ctx_kw):
    tmp_path.mkdir(parents=True, exist_ok=True)
    return IMPLEMENTATIONS["silence"](
        in_wav, params or {}, _ctx(tmp_path, in_wav, **ctx_kw)
    )


def _copy(src: Path, tmp_path: Path, name: str = "in.wav") -> Path:
    dst = tmp_path / name
    shutil.copyfile(src, dst)
    return dst


# ---- remove vs shorten ----------------------------------------------------------


def test_remove_default_drops_silence(tmp_path):
    src = _copy(SPEECH_SIL, tmp_path)
    res = _run(src, tmp_path)
    in_dur, out_dur = _dur(src), _dur(res.path)
    # the two fixture silences (3 + 5 s) plus a few sentence pauses go
    assert out_dur < in_dur - 7.5
    assert out_dur > in_dur - 12.0
    # timeline: out_duration matches the rendered wav
    assert res.timeline is not None
    assert res.timeline.out_duration == pytest.approx(out_dur, abs=0.01)


def test_shorten_keeps_pause_per_gap(tmp_path):
    src = _copy(SPEECH_SIL, tmp_path)
    # separate work dirs — stage_wav writes the same stage filename
    removed = _run(src, tmp_path / "rm")
    shortened = _run(
        src, tmp_path / "sh",
        {"action": "shorten", "keep_pause_s": 0.25},
    )
    r_dur = _dur(removed.path)
    s_dur = _dur(shortened.path)
    # shorten keeps ≤0.25 s per long gap — longer than remove, shorter
    # than the input
    in_dur = _dur(src)
    assert s_dur > r_dur
    assert s_dur < in_dur - 6.0  # most of the 8 s still gone
    # each detected long gap contributes ~0.25 s back
    assert s_dur - r_dur <= 0.25 * 8 + 0.5


def test_amplitude_mode_cuts_tone_gaps(tmp_path):
    """Volume-threshold mode finds the digital-silence gaps the VAD
    would miss (tones aren't speech)."""
    from tests.test_silence_detect import _tone_bursts

    src = _tone_bursts(tmp_path / "tones.wav")
    res = IMPLEMENTATIONS["silence"](
        tmp_path / "tones.wav",
        {"mode": "amplitude", "min_silence_s": 0.25, "action": "remove"},
        _ctx(tmp_path, tmp_path / "tones.wav"),
    )
    # 5.5 s in; the 2 s + 0.5 s gaps are cut; pad 0.1 keeps ~0.2 s/gap
    assert _dur(res.path) == pytest.approx(3.4, abs=0.3)
    assert res.message_code == "silence_removed"
    assert res.message_params["count"] == 2


# ---- no speech / nothing to cut --------------------------------------------------


def test_silence_only_file_passes_unchanged(tmp_path):
    src = _copy(SILENCE, tmp_path)
    res = _run(src, tmp_path)
    assert _dur(res.path) == pytest.approx(_dur(src), abs=0.01)
    assert res.message_code == "no_speech_found"
    assert "No speech" in res.message
    assert res.timeline is None  # unchanged — identity stays


def test_all_speech_reports_zero_cuts(tmp_path):
    """min_silence_s above every real pause → nothing to cut → 0/0."""
    src = _copy(SPEECH, tmp_path)
    res = _run(src, tmp_path, {"min_silence_s": 3.0})
    assert _dur(res.path) == pytest.approx(_dur(src), abs=0.01)
    assert res.message_code == "silence_removed"
    assert res.message_params == {"seconds": 0.0, "count": 0}


# ---- summary message ----------------------------------------------------------------


def test_summary_message_code_and_params(tmp_path):
    src = _copy(SPEECH_SIL, tmp_path)
    res = _run(src, tmp_path)
    assert res.message_code == "silence_removed"
    p = res.message_params
    assert p["count"] >= 2  # the two fixture silences
    assert p["seconds"] == pytest.approx(
        _dur(src) - _dur(res.path), abs=0.15
    )
    assert res.message == (
        f"Removed {p['seconds']} s of silence in {p['count']} places"
    )


# ---- timeline + video snapping -------------------------------------------------------


def test_timeline_composes_with_parent_and_snaps(tmp_path):
    """A prior trim (parent timeline) + fps=25: cut boundaries land on
    the frame grid in source coordinates."""
    src = _copy(SPEECH_SIL, tmp_path)
    parent = Timeline.identity(_dur(src)).keep([(0.0, 23.0)])
    res = _run(src, tmp_path, None, fps=25.0, timeline=parent)
    assert res.timeline is not None
    q = 1.0 / 25.0
    for seg in res.timeline.segments:
        for t in (seg.src_start, seg.src_end):
            assert abs(t / q - round(t / q)) < 0.5, (t, res.timeline)
    assert res.timeline.out_duration == pytest.approx(
        _dur(res.path), abs=0.05
    )


def test_output_duration_equals_timeline(tmp_path):
    src = _copy(SPEECH_SIL, tmp_path)
    res = _run(src, tmp_path, None, fps=30.0)
    assert _dur(res.path) == pytest.approx(
        res.timeline.out_duration, abs=1.0 / 30.0 + 0.01
    )


# ---- cancel ----------------------------------------------------------------------


def test_cancel_during_detection(tmp_path):
    from backend.pipeline.types import CancelledError

    src = _copy(SPEECH_SIL, tmp_path)
    with pytest.raises(CancelledError):
        _run(src, tmp_path, should_cancel=lambda: True)


# ---- Review Focus 2: speech preservation on real recordings ---------------------


@pytest.mark.parametrize(
    "name",
    ["Recording.m4a", "Recording (2).m4a", "Recording (3).m4a"],
)
def test_real_recording_keeps_speech(tmp_path, real_dir, name):
    """VAD speech total of the output ≥ 95 % of the input's (M5 RF2)."""
    from backend.media.decode import decode_to_wav
    from backend.media.silence_detect import speech_intervals_vad

    src = real_dir / name
    in_wav = tmp_path / "in.wav"
    decode_to_wav(src, in_wav, 0)
    res = _run(in_wav, tmp_path)
    in_speech = sum(
        b - a for a, b in speech_intervals_vad(in_wav)
    )
    out_speech = sum(
        b - a for a, b in speech_intervals_vad(res.path)
    )
    assert out_speech >= 0.95 * in_speech, (
        f"{name}: output speech {out_speech:.2f}s < 95% of "
        f"input {in_speech:.2f}s"
    )
    assert _dur(res.path) < _dur(in_wav)  # something was cut
