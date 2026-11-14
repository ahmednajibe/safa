"""M4b Task 2: sample-accurate keep-interval audio renderer.

``render_keep`` concatenates the kept spans of a 48 kHz WAV with short
non-overlapping raised-cosine fades *inside* each segment, so the output is
exactly Σ kept lengths long and boundary joins are click-free (SPEC §8).
"""
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from backend.media.cutter import render_keep
from backend.pipeline.types import CancelledError

SR = 48000


def _write(path: Path, sig: np.ndarray) -> Path:
    sf.write(str(path), sig, SR, subtype="FLOAT")
    return path


def _sine(freq: float, seconds: float, amp: float = 0.5) -> np.ndarray:
    t = np.arange(int(SR * seconds)) / SR
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def test_render_keep_length_exact(tmp_path):
    src = _write(tmp_path / "in.wav", _sine(440, 5.0))
    out = tmp_path / "out.wav"
    n = render_keep(src, out, [(0.5, 2.0), (3.0, 4.25)])
    assert n == round((2.0 - 0.5) * SR) + round((4.25 - 3.0) * SR)
    assert sf.info(str(out)).frames == n


def test_render_keep_whole_file(tmp_path):
    src = _write(tmp_path / "in.wav", _sine(440, 1.0))
    out = tmp_path / "out.wav"
    n = render_keep(src, out, [(0.0, 1.0)])
    assert n == SR


def test_render_keep_stereo_and_content(tmp_path):
    sig = np.stack([_sine(440, 2.0), _sine(880, 2.0)], axis=1)
    src = _write(tmp_path / "in.wav", sig)
    out = tmp_path / "out.wav"
    n = render_keep(src, out, [(0.25, 1.0)])
    assert n == round(0.75 * SR)
    data, sr = sf.read(str(out), dtype="float32", always_2d=True)
    assert data.shape == (n, 2)
    # fades start/end at zero gain — first and last samples are silent
    assert np.abs(data[0]).max() < 1e-4
    assert np.abs(data[-1]).max() < 1e-4
    # interior equals source samples from 0.25 s on
    mid = n // 2
    src_at = 0.25 + mid / SR
    assert np.allclose(
        data[mid],
        [0.5 * np.sin(2 * np.pi * 440 * src_at),
         0.5 * np.sin(2 * np.pi * 880 * src_at)],
        atol=1e-3,
    )


def test_boundaries_are_click_free(tmp_path):
    """Review Focus 4: 1 kHz sine cut in 10 places — max sample-to-sample
    jump in the output ≤ max jump of the plain sine × 1.5."""
    src = _write(tmp_path / "in.wav", _sine(1000, 10.0))
    out = tmp_path / "out.wav"
    # keep nine 0.7 s islands → 10 cut joins
    keep = [(i + 0.15, i + 0.85) for i in range(9)]
    n = render_keep(src, out, keep)
    assert n == 9 * round(0.7 * SR)
    plain = _sine(1000, 1.0)
    ref_jump = float(np.abs(np.diff(plain)).max())
    data, _ = sf.read(str(out), dtype="float32")
    out_jump = float(np.abs(np.diff(data)).max())
    assert out_jump <= ref_jump * 1.5


def test_fade_stays_inside_segment(tmp_path):
    """Fades never overlap into neighbouring segments: with a 5 s keep the
    middle 4 s are untouched (fade ≤ 10 ms each side)."""
    src = _write(tmp_path / "in.wav", _sine(1000, 10.0, amp=0.4))
    out = tmp_path / "out.wav"
    render_keep(src, out, [(2.0, 7.0)], fade_ms=10.0)
    data, _ = sf.read(str(out), dtype="float32")
    interior = data[int(0.02 * SR): int(4.98 * SR)]
    expected = 0.4 * np.sin(
        2 * np.pi * 1000
        * (2.0 + np.arange(len(interior)) / SR + 0.02)
    )
    assert np.abs(interior - expected).max() < 1e-3


def test_render_keep_empty(tmp_path):
    src = _write(tmp_path / "in.wav", _sine(440, 1.0))
    out = tmp_path / "out.wav"
    assert render_keep(src, out, []) == 0
    assert sf.info(str(out)).frames == 0


def test_render_keep_cancel(tmp_path):
    src = _write(tmp_path / "in.wav", _sine(440, 5.0))
    out = tmp_path / "out.wav"
    calls = iter(range(100))

    def cancel():
        return next(calls) >= 2

    with pytest.raises(CancelledError):
        render_keep(src, out, [(0.0, 5.0)], should_cancel=cancel)
    assert not out.exists()


def test_render_keep_reports_progress(tmp_path):
    src = _write(tmp_path / "in.wav", _sine(440, 3.0))
    out = tmp_path / "out.wav"
    pcts = []
    render_keep(src, out, [(0.0, 3.0)],
                report=lambda p, m=None: pcts.append(p))
    assert pcts[-1] >= 0.99
