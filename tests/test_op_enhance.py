"""M4a Task 3: enhance — pedalboard voice chain + optional ffmpeg deesser."""
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


def _ctx(tmp_path: Path, in_wav: Path, reports: list | None = None) -> OpContext:
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
        should_cancel=lambda: False,
    )


def _band_db(path: Path, lo: float, hi: float) -> float:
    x, sr = sf.read(str(path), dtype="float32", always_2d=True)
    sig = x.mean(axis=1)
    power = np.abs(np.fft.rfft(sig)) ** 2
    f = np.fft.rfftfreq(len(sig), 1 / sr)
    m = (f >= lo) & (f <= hi)
    return 10 * np.log10(power[m].mean() + 1e-20)


def _noise(seconds: float = 6.0, stereo: bool = True) -> np.ndarray:
    rng = np.random.default_rng(42)
    n = int(SR * seconds)
    x = rng.standard_normal((n, 2) if stereo else n) * 0.05
    return x.astype(np.float32)


def test_enhance_preserves_length_and_stays_finite(tmp_path):
    src = _write(tmp_path / "in.wav", _noise())
    res = IMPLEMENTATIONS["enhance"](
        src, {"preset": "voice", "deess": False}, _ctx(tmp_path, src)
    )
    info = sf.info(str(res.path))
    assert info.frames == sf.info(str(src)).frames  # exact, not ±1
    out, _ = sf.read(str(res.path), dtype="float32")
    assert np.isfinite(out).all()


def test_enhance_mono_stays_mono_stereo_stays_stereo(tmp_path):
    """Review Focus 2: channel layout is preserved through the chain."""
    for stereo in (False, True):
        src = _write(
            tmp_path / ("in_st.wav" if stereo else "in_mo.wav"),
            _noise(seconds=2.0, stereo=stereo),
        )
        res = IMPLEMENTATIONS["enhance"](
            src, {"preset": "voice", "deess": True},
            _ctx(tmp_path, src),
        )
        assert sf.info(str(res.path)).channels == (2 if stereo else 1)


def test_enhance_voice_presence_tilt(tmp_path):
    """voice preset: 3.5 kHz band gains vs the 250 Hz dip on white noise."""
    src = _write(tmp_path / "in.wav", _noise())
    tilt_in = _band_db(src, 3200, 3800) - _band_db(src, 200, 300)

    res = IMPLEMENTATIONS["enhance"](
        src, {"preset": "voice", "deess": False}, _ctx(tmp_path, src)
    )
    tilt_out = _band_db(res.path, 3200, 3800) - _band_db(res.path, 200, 300)
    assert tilt_out - tilt_in >= 3.0, (
        f"expected >=3 dB presence tilt, got {tilt_out - tilt_in:+.2f} dB"
    )


def test_enhance_presets_accepted(tmp_path):
    src = _write(tmp_path / "in.wav", _noise(seconds=1.0, stereo=False))
    for preset in ("voice", "bright", "warm"):
        res = IMPLEMENTATIONS["enhance"](
            src, {"preset": preset, "deess": False}, _ctx(tmp_path, src)
        )
        assert res.path.is_file()


def test_enhance_deess_false_skips_ffmpeg(tmp_path, monkeypatch):
    """deess=False must not run the ffmpeg deesser pass at all."""
    import backend.operations.impl.enhance as enh

    calls = []
    monkeypatch.setattr(
        enh, "run_filter", lambda *a, **k: calls.append((a, k))
    )
    src = _write(tmp_path / "in.wav", _noise(seconds=2.0, stereo=False))

    IMPLEMENTATIONS["enhance"](
        src, {"preset": "voice", "deess": False}, _ctx(tmp_path, src)
    )
    assert calls == []

    IMPLEMENTATIONS["enhance"](
        src, {"preset": "voice", "deess": True}, _ctx(tmp_path, src)
    )
    assert len(calls) == 1 and "deesser" in calls[0][0][2]


def test_enhance_reports_progress_per_block(tmp_path):
    src = _write(tmp_path / "in.wav", _noise(seconds=25.0, stereo=False))
    reports: list = []
    IMPLEMENTATIONS["enhance"](
        src, {"preset": "voice", "deess": False}, _ctx(tmp_path, src, reports)
    )
    pcts = [p for p, _ in reports]
    assert len(pcts) >= 3, f"expected >=3 block reports, got {pcts}"
    assert pcts == sorted(pcts)
    assert pcts[-1] == pytest.approx(1.0)


def test_enhance_cancel_between_blocks(tmp_path):
    from backend.pipeline.types import CancelledError

    src = _write(tmp_path / "in.wav", _noise(seconds=25.0, stereo=False))
    ctx = _ctx(tmp_path, src)
    calls = iter(range(100))
    ctx.should_cancel = lambda: next(calls) >= 2  # True from 3rd block check
    with pytest.raises(CancelledError):
        IMPLEMENTATIONS["enhance"](
            src, {"preset": "voice", "deess": False}, ctx
        )
    assert not (tmp_path / "enhance_pedalboard.wav").exists()
