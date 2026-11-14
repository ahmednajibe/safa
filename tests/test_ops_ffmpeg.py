"""M4a Task 2: ffmpeg ops — channel, highpass, dehum, declick.

Measured on synthetic signals written with numpy+soundfile: tone levels are
read back with a Hann-windowed FFT peak around the target bin; ratios cancel
the window/normalisation, so the dB numbers are honest.
"""
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from backend.operations.base import OpContext
from backend.operations.impl import IMPLEMENTATIONS

SR = 48000


def _write(path: Path, sig: np.ndarray) -> Path:
    sf.write(str(path), sig, SR, subtype="FLOAT")
    return path


def _tone_db(path: Path, freq: float, channel: int | None = None) -> float:
    x, sr = sf.read(str(path), dtype="float32", always_2d=True)
    sig = x[:, channel or 0]
    w = np.hanning(len(sig))
    spectrum = np.abs(np.fft.rfft(sig * w))
    b = int(round(freq * len(sig) / sr))
    peak = spectrum[max(0, b - 2):b + 3].max()
    return 20 * np.log10(peak / (w.sum() / 2) + 1e-12)


def _ctx(tmp_path: Path, in_wav: Path, reports: list | None = None,
         cancel=None) -> OpContext:
    info = sf.info(str(in_wav))
    return OpContext(
        work_dir=tmp_path,
        device="cpu",
        duration_s=info.frames / info.samplerate,
        sample_rate=info.samplerate,
        channels=info.channels,
        report=(
            (lambda p, m: reports.append((p, m)))
            if reports is not None else (lambda p, m: None)
        ),
        should_cancel=cancel or (lambda: False),
    )


def _run(op_id: str, in_wav: Path, tmp_path: Path, params: dict | None = None,
         reports: list | None = None):
    ctx = _ctx(tmp_path, in_wav, reports=reports)
    return IMPLEMENTATIONS[op_id](in_wav, params or {}, ctx)


def _sine(freq: float, seconds: float = 3.0, amp: float = 0.3) -> np.ndarray:
    t = np.arange(int(SR * seconds)) / SR
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def _frames(path: Path) -> int:
    return sf.info(str(path)).frames


# ---- highpass ---------------------------------------------------------------


def test_highpass_kills_rumble_keeps_speech(tmp_path):
    src = _write(tmp_path / "in.wav", _sine(30) + _sine(1000))
    before_30, before_1k = _tone_db(src, 30), _tone_db(src, 1000)

    res = _run("highpass", src, tmp_path, {"cutoff_hz": 75})

    assert before_30 - _tone_db(res.path, 30) >= 18.0
    assert abs(before_1k - _tone_db(res.path, 1000)) <= 1.0
    assert abs(_frames(res.path) - _frames(src)) <= 1


# ---- dehum ------------------------------------------------------------------


def test_dehum_removes_fundamental_and_harmonic(tmp_path):
    sig = _sine(50, amp=0.2) + _sine(150, amp=0.15) + _sine(1000, amp=0.3)
    src = _write(tmp_path / "in.wav", sig)
    b50, b150, b1k = (_tone_db(src, f) for f in (50, 150, 1000))

    res = _run("dehum", src, tmp_path, {"base": 50, "harmonics": 4})

    assert b50 - _tone_db(res.path, 50) >= 20.0
    assert b150 - _tone_db(res.path, 150) >= 20.0
    assert abs(b1k - _tone_db(res.path, 1000)) <= 1.0
    assert abs(_frames(res.path) - _frames(src)) <= 1


def test_dehum_frequency_param_honoured(tmp_path):
    """Review Focus 4: dehum set to 60 Hz must NOT remove a 50 Hz hum."""
    sig = _sine(50, amp=0.2) + _sine(1000, amp=0.3)
    src = _write(tmp_path / "in.wav", sig)
    b50 = _tone_db(src, 50)

    res_wrong = _run("dehum", src, tmp_path, {"base": 60, "harmonics": 4})
    # measure now — the next run reuses the same stage filename
    wrong_att = b50 - _tone_db(res_wrong.path, 50)
    res_right = _run("dehum", src, tmp_path, {"base": 50, "harmonics": 4})
    right_att = b50 - _tone_db(res_right.path, 50)

    assert wrong_att < 6.0, \
        "60 Hz setting removed the 50 Hz hum — frequency param ignored?"
    assert right_att >= 20.0


# ---- declick -----------------------------------------------------------------


def test_declick_removes_clicks(tmp_path):
    rng = np.random.default_rng(7)
    sig = _sine(440, seconds=2.0, amp=0.1)
    clicks = rng.choice(np.arange(4800, len(sig) - 4800), size=20,
                        replace=False)
    sig[clicks] = rng.choice([-1.0, 1.0], size=20)
    src = _write(tmp_path / "in.wav", sig)
    before = int((np.abs(sig) > 0.5).sum())
    assert before >= 20

    res = _run("declick", src, tmp_path, {"sensitivity": "normal"})

    after_sig, _ = sf.read(str(res.path), dtype="float32")
    after = int((np.abs(after_sig) > 0.5).sum())
    assert after <= before * 0.2, f"{before} clicks before, {after} after"
    assert abs(_frames(res.path) - _frames(src)) <= 1


def test_declick_strength_map_accepted(tmp_path):
    src = _write(tmp_path / "in.wav", _sine(440, seconds=1.0))
    for mode in ("gentle", "normal", "strong"):
        res = _run("declick", src, tmp_path, {"sensitivity": mode})
        assert res.path.is_file()


# ---- channel -----------------------------------------------------------------


def _stereo_fixture(tmp_path: Path) -> Path:
    left = _sine(440, seconds=2.0, amp=0.2)
    right = _sine(880, seconds=2.0, amp=0.2)
    return _write(tmp_path / "stereo.wav", np.stack([left, right], axis=1))


def test_channel_keep_stays_stereo(tmp_path):
    src = _stereo_fixture(tmp_path)
    res = _run("channel", src, tmp_path, {"mode": "keep"})
    info = sf.info(str(res.path))
    assert info.channels == 2
    assert info.frames == _frames(src)


def test_channel_mono_downmixes(tmp_path):
    src = _stereo_fixture(tmp_path)
    res = _run("channel", src, tmp_path, {"mode": "mono"})
    info = sf.info(str(res.path))
    assert info.channels == 1
    # both source tones survive in the mix (each ~-6 dB relative)
    assert _tone_db(res.path, 440) > -20
    assert _tone_db(res.path, 880) > -20
    assert abs(_frames(res.path) - _frames(src)) <= 1


def test_channel_left_right_pick_sides(tmp_path):
    src = _stereo_fixture(tmp_path)
    for mode, keep, drop in (("left", 440, 880), ("right", 880, 440)):
        res = _run("channel", src, tmp_path, {"mode": mode})
        info = sf.info(str(res.path))
        assert info.channels == 1
        assert _tone_db(res.path, keep) - _tone_db(res.path, drop) > 20


def test_channel_mono_input_unchanged(tmp_path):
    src = _write(tmp_path / "mono.wav", _sine(440, seconds=1.0))
    for mode in ("mono", "left", "right"):
        res = _run("channel", src, tmp_path, {"mode": mode})
        info = sf.info(str(res.path))
        assert info.channels == 1
        assert info.frames == _frames(src)


# ---- wiring -------------------------------------------------------------------


def test_implementations_registered():
    for op in ("channel", "highpass", "dehum", "declick"):
        assert op in IMPLEMENTATIONS, f"{op} not registered"


def test_reports_progress(tmp_path):
    src = _write(tmp_path / "in.wav", _sine(440, seconds=2.0))
    reports: list = []
    _run("highpass", src, tmp_path, {"cutoff_hz": 75}, reports=reports)
    pcts = [p for p, _ in reports]
    assert pcts and pcts[-1] >= 0.99
