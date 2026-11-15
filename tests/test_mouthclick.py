"""M4c Task 1: mouth-click detector + band-limited AR repair.

Covers the plan's acceptance tests: injected-click recall/false-positives,
band-split reconstruction exactness, near-no-op on click-free speech
(Review Focus 5), consonant safety on the real recordings (Review Focus 1),
and the ≥60 % real-recording click reduction.
"""
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from backend.media.decode import decode_to_wav
from backend.media.probe import probe
from backend.operations.base import OpContext
from backend.operations.impl import IMPLEMENTATIONS
from tests.audio_metrics import (
    band_rms_db,
    consonant_windows,
    load_mono,
    window_band_rms_db,
)

SR = 48000
GEN = Path(__file__).parent / "fixtures" / "generated"


def _ctx(tmp_path: Path, in_wav: Path) -> OpContext:
    info = sf.info(str(in_wav))
    return OpContext(
        work_dir=tmp_path,
        device="cpu",
        duration_s=info.frames / info.samplerate,
        sample_rate=info.samplerate,
        channels=info.channels,
        report=lambda p, m: None,
        should_cancel=lambda: False,
    )


def _write(path: Path, sig: np.ndarray, sr: int = SR) -> Path:
    sf.write(str(path), sig, sr, subtype="FLOAT")
    return path


def _decode(real: Path, tmp_path: Path) -> Path:
    p = probe(real)
    return decode_to_wav(real, tmp_path / (real.stem + ".wav"),
                         p.audio[0].index).path


def _click(sig: np.ndarray, pos: int, dur_ms: float, amp: float,
           freq: float, sr: int = SR) -> tuple[int, int]:
    """Inject a 1–4 ms band-limited click (windowed high-band tone burst)."""
    n = max(1, int(sr * dur_ms / 1000))
    t = np.arange(n) / sr
    sig[pos:pos + n] += (amp * np.sin(2 * np.pi * freq * t)
                       * np.hanning(n)).astype(np.float32)
    return pos, pos + n


def _inject_clicks(x: np.ndarray, sr: int, count: int = 30,
                   seed: int = 3) -> list[tuple[int, int]]:
    rng = np.random.default_rng(seed)
    margin = int(sr * 0.8)
    # evenly spread with jitter so exactly ``count`` clicks land ≥0.55 s apart
    slots = np.linspace(margin, len(x) - margin, count + 2)[1:-1]
    positions = slots + rng.uniform(-0.15, 0.15, len(slots)) * sr
    spans = []
    for pos in positions:
        spans.append(_click(
            x, int(pos), dur_ms=rng.uniform(1.0, 4.0),
            amp=rng.uniform(0.09, 0.18), freq=rng.uniform(4000, 9000)))
    return spans


def _hits(spans: list[tuple[int, int]], truth: list[tuple[int, int]],
          sr: int = SR) -> tuple[int, int]:
    """(detected-truth, false-positives): a span counts as a hit if it
    overlaps the injected span ±4 ms."""
    pad = int(0.004 * sr)
    detected = 0
    for ta, tb in truth:
        if any(a < tb + pad and b > ta - pad for a, b in spans):
            detected += 1
    fps = sum(
        0 if any(a < tb + pad and b > ta - pad for ta, tb in truth) else 1
        for a, b in spans
    )
    return detected, fps


# ---- detector ------------------------------------------------------------


def test_band_split_reconstruction_is_exact():
    """low+high must rebuild the input within −80 dB (plan Task 1)."""
    from backend.media.mouthclick import split_bands

    rng = np.random.default_rng(0)
    x = rng.standard_normal(SR * 2).astype(np.float32) * 0.05
    low, high = split_bands(x, SR)
    resid = low + high - x
    resid_db = 20 * np.log10(
        np.sqrt((resid ** 2).mean() / (x ** 2).mean())
    )
    assert resid_db < -80.0


def test_detects_injected_clicks_recall_and_false_positives(generated_fixtures):
    """30 injected clicks in SAPI speech: ≥80 % found, ≤2 FP per 10 s."""
    from backend.media.mouthclick import detect_mouth_clicks

    x, sr = load_mono(GEN / "speech_silences.wav")  # 23.66 s
    truth = _inject_clicks(x, sr, count=30)
    spans = detect_mouth_clicks(x, sr)
    detected, fps = _hits(spans, truth)
    assert detected >= 0.8 * len(truth), (
        f"recall {detected}/{len(truth)} below 80 %"
    )
    assert fps <= 2 * len(x) / sr / 10.0, f"{fps} false positives"


def test_repair_attenuates_click_band():
    """After repair, the click span's 2.5–12 kHz energy drops ≥10 dB."""
    from backend.media.mouthclick import (
        detect_mouth_clicks,
        repair_mouth_clicks,
    )

    rng = np.random.default_rng(5)
    t = np.arange(SR * 4) / SR
    x = (0.1 * np.sin(2 * np.pi * 300 * t)
         + 0.02 * rng.standard_normal(len(t))).astype(np.float32)
    truth = [_click(x, int(2.0 * SR), 2.0, 0.2, 6000)]
    spans = detect_mouth_clicks(x, SR)
    assert spans, "injected click not detected"
    a, b = truth[0]
    before = band_rms_db(x[a - 240:b + 240], SR, 2500, 12000)
    y = repair_mouth_clicks(x, SR, spans)
    after = band_rms_db(y[a - 240:b + 240], SR, 2500, 12000)
    assert before - after >= 10.0
    assert len(y) == len(x)


def test_detect_empty_input_and_silence():
    from backend.media.mouthclick import detect_mouth_clicks

    assert detect_mouth_clicks(np.zeros(1000, np.float32), SR) == []
    assert detect_mouth_clicks(np.zeros(0, np.float32), SR) == []


# ---- the op (mouth mode = default) ----------------------------------------


def test_declick_near_noop_on_click_free_speech(tmp_path, generated_fixtures):
    """Review Focus 5: a click-free file passes through — residual < −60 dB."""
    src = GEN / "speech.wav"
    res = IMPLEMENTATIONS["declick"](
        src, {"mode": "mouth", "sensitivity": "normal"}, _ctx(tmp_path, src)
    )
    xin, _ = load_mono(src)
    xout, _ = load_mono(res.path)
    d = xout - xin
    resid_db = 20 * np.log10(
        np.sqrt((d ** 2).mean() / (xin ** 2).mean())
    )
    assert resid_db < -60.0, f"residual {resid_db:.1f} dB — speech touched"


def test_declick_mouth_preserves_length_and_channels(tmp_path):
    rng = np.random.default_rng(9)
    stereo = (rng.standard_normal((SR * 3, 2)) * 0.02).astype(np.float32)
    src = _write(tmp_path / "in.wav", stereo)
    res = IMPLEMENTATIONS["declick"](
        src, {"mode": "mouth"}, _ctx(tmp_path, src)
    )
    info = sf.info(str(res.path))
    assert info.channels == 2
    assert info.frames == sf.info(str(src)).frames


def test_declick_click_on_block_boundary(tmp_path):
    """A click straddling the 10 s streaming-block edge must still be
    removed — exercises the overlap logic (plan global constraints)."""
    from backend.media.mouthclick import detect_mouth_clicks

    src_gen = GEN / "speech.wav"
    x, sr = load_mono(src_gen)
    tiled = np.tile(x, 5)[: int(25.5 * sr)]  # ~26 s of speech
    pos = int(10.0 * sr) - 24  # click centre ~0.5 ms before the 10 s seam
    _click(tiled, pos, 2.5, 0.2, 6500)
    src = _write(tmp_path / "in.wav", tiled)
    res = IMPLEMENTATIONS["declick"](
        src, {"mode": "mouth", "sensitivity": "normal"}, _ctx(tmp_path, src)
    )
    y, _ = load_mono(res.path)
    around = detect_mouth_clicks(y[pos - 4800:pos + 4800], sr)
    assert not around, f"boundary click survived at {around}"


def test_declick_crackle_mode_still_available(tmp_path):
    """mode=crackle keeps the ffmpeg adeclick path (digital crackle)."""
    t = np.arange(SR * 2) / SR
    sig = (0.1 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    idx = np.arange(2400, len(sig) - 2400, 48000 // 17)
    sig[idx] = 1.0
    src = _write(tmp_path / "in.wav", sig)
    res = IMPLEMENTATIONS["declick"](
        src, {"mode": "crackle", "sensitivity": "normal"}, _ctx(tmp_path, src)
    )
    y, _ = load_mono(res.path)
    assert int((np.abs(y) > 0.5).sum()) <= 4


# ---- real recordings ------------------------------------------------------


def _real_files(real_dir) -> list[Path]:
    return sorted(real_dir.glob("*.m4a"))


def test_declick_real_recordings_click_reduction(tmp_path, real_dir):
    """Detector-counted clicks on the output drop ≥60 % vs input (plan)."""
    from backend.media.mouthclick import detect_mouth_clicks

    report = []
    for real in _real_files(real_dir):
        src = _decode(real, tmp_path)
        xin, sr = load_mono(src)
        res = IMPLEMENTATIONS["declick"](
            src, {"mode": "mouth", "sensitivity": "normal"},
            _ctx(tmp_path, src),
        )
        xout, _ = load_mono(res.path)
        n_in = len(detect_mouth_clicks(xin, sr))
        n_out = len(detect_mouth_clicks(xout, sr))
        report.append(f"{real.name}: {n_in} -> {n_out}")
        assert n_out <= 0.4 * n_in or n_out <= 1, (
            f"{real.name}: clicks {n_in} -> {n_out} (<60 % reduction)"
        )
    print("\nclick counts (detector, normal): " + "; ".join(report))


def test_declick_consonant_safety_real(tmp_path, real_dir):
    """Review Focus 1: 2–8 kHz RMS inside plosive/fricative windows changes
    ≤1.5 dB after declick on each real recording."""
    for real in _real_files(real_dir):
        src = _decode(real, tmp_path)
        xin, sr = load_mono(src)
        windows = consonant_windows(xin, sr)
        assert windows, f"no consonant windows found in {real.name}"
        before = window_band_rms_db(xin, sr, windows, 2000, 8000)
        res = IMPLEMENTATIONS["declick"](
            src, {"mode": "mouth", "sensitivity": "normal"},
            _ctx(tmp_path, src),
        )
        xout, _ = load_mono(res.path)
        after = window_band_rms_db(xout, sr, windows, 2000, 8000)
        deltas = [abs(a - b) for a, b in zip(after, before)]
        worst = max(deltas)
        assert worst <= 1.5, (
            f"{real.name}: consonant window changed {worst:.2f} dB"
        )
