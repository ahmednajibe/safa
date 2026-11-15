"""M4a Task 3: enhance — pedalboard voice chain + optional ffmpeg deesser.

M4c Task 2 reworks the chain: dynamic per-file NoiseGate expander, 95 Hz
double highpass, switchable de-boom EQ, per-file compressor threshold,
retuned presence — plus ``level_stats`` in backend.media.loudness and the
Review Focus 3/4 real-recording tests.
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
    level_metrics,
    quiet_band_share_db,
    speech_band_ratio_db,
)

SR = 48000
GEN = Path(__file__).parent / "fixtures" / "generated"
REAL = Path(__file__).parent / "fixtures" / "real"


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


# ---- M4c Task 2: level_stats + the dynamic voice chain ----------------------


def _mono(path: Path) -> np.ndarray:
    x, _ = sf.read(str(path), dtype="float32", always_2d=True)
    return x.mean(axis=1)


def _enhance(src: Path, tmp_path: Path, **params) -> Path:
    p = {"preset": "voice", "deess": False}
    p.update(params)
    return IMPLEMENTATIONS["enhance"](src, p, _ctx(tmp_path, src)).path


def _vad_segments(x: np.ndarray, sr: int,
                  min_s: float = 0.15, merge_s: float = 0.15) -> list:
    """Deterministic energy VAD for tests: 25 ms frames, threshold = 15 dB
    over the frame-level 20th percentile."""
    from tests.audio_metrics import envelope_db

    env, win, hop = envelope_db(x, sr, win_ms=25.0, hop_ms=25.0)
    if len(env) == 0:
        return []
    floor = np.percentile(env, 20)
    active = env > floor + 15.0
    d = np.diff(active.astype(np.int8), prepend=0, append=0)
    segs = [[int(a * hop), int(b * hop)] for a, b in
            zip(np.where(d == 1)[0], np.where(d == -1)[0])]
    merged: list = []
    for a, b in segs:
        if merged and a - merged[-1][1] <= merge_s * sr:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged if b - a >= min_s * sr]


def _pink_noise(n: int, sr: int, seed: int = 1) -> np.ndarray:
    """Deterministic pink-ish noise via 1/sqrt(f) spectral shaping."""
    rng = np.random.default_rng(seed)
    spec = rng.standard_normal(n // 2 + 1) + 1j * rng.standard_normal(n // 2 + 1)
    f = np.fft.rfftfreq(n, 1 / sr)
    spec[1:] /= np.sqrt(f[1:])
    spec[0] = 0.0
    return np.fft.irfft(spec, n).astype(np.float32)


def test_level_stats_20ms_percentiles(tmp_path):
    """floor = 20th-pct and speech = 85th-pct of the 20 ms frame RMS."""
    from backend.media.loudness import level_stats

    sr = SR
    t = np.arange(sr * 8) / sr
    loud = (0.1 * np.sin(2 * np.pi * 300 * t)).astype(np.float32)   # ≈ −23 dBFS
    quiet = (rng := np.random.default_rng(0)).standard_normal(
        len(t)).astype(np.float32) * (10 ** (-70 / 20))            # ≈ −70 dBFS
    x = np.where((t % 2) < 1, loud, quiet)
    path = _write(tmp_path / "alt.wav", x)
    st = level_stats(path)
    assert st["floor_dbfs"] == pytest.approx(-70, abs=4)
    assert st["speech_dbfs"] == pytest.approx(-23, abs=4)


def test_expander_pushes_down_quiet_floor(tmp_path):
    """Floor well below speech must get ducked by the downward expander —
    the M4a chain left it untouched (or raised it via make-up gain).

    The floor is crest-limited noise (peaks ≤ ~8 dB over RMS — like real
    hiss); broadband white noise peaks ~13 dB over RMS would reopen any
    gate whose threshold sits only 10 dB above the floor RMS."""
    sr = SR
    t = np.arange(sr * 6) / sr
    rng = np.random.default_rng(2)
    speech = 0.1 * np.sin(2 * np.pi * 220 * t)
    floor = rng.standard_normal(len(t))
    floor = np.clip(floor, -2.0, 2.0)            # crest ≈ 6 dB
    floor *= 10 ** (-65 / 20) / np.sqrt((floor ** 2).mean())
    x = (np.where((t % 2) < 1, speech, 0.0) + floor).astype(np.float32)
    src = _write(tmp_path / "in.wav", x)
    res = _enhance(src, tmp_path)
    m_in = level_metrics(x, sr)
    m_out = level_metrics(_mono(res), sr)
    assert m_out["floor_dbfs"] - m_in["floor_dbfs"] <= -3.0, (
        f"floor only moved {m_out['floor_dbfs'] - m_in['floor_dbfs']:+.1f} dB"
    )


def test_reduce_boom_toggles_boom_filters(tmp_path):
    """reduce_boom=False must leave the 100–180 Hz region alone."""
    src = _write(tmp_path / "in.wav", _noise())
    (tmp_path / "on").mkdir()
    (tmp_path / "off").mkdir()
    on = _enhance(src, tmp_path / "on", reduce_boom=True)
    off = _enhance(src, tmp_path / "off", reduce_boom=False)
    d = _band_db(off, 90, 180) - _band_db(on, 90, 180)
    assert d >= 2.0, f"boom EQ did nothing ({d:.2f} dB difference)"


def _deep_floor_db(x: np.ndarray, sr: int) -> float:
    """level_metrics floor with a −240 dB epsilon — the expander legitimately
    drives pause floors below the 1e-9 clamp, and comparing two clamped
    floors would hide real differences."""
    n = int(sr * 0.05)
    f = x[: len(x) // n * n].reshape(-1, n)
    r = np.sort(20 * np.log10(np.maximum(
        np.sqrt((f * f).mean(axis=1)), 1e-12)))
    return float(r[: max(1, len(r) // 10)].mean())


def test_enhance_thresholds_scale_with_input(tmp_path, generated_fixtures):
    """Review Focus 4: a −20 dB-scaled input must receive equivalent
    *relative* processing — after loudness normalisation all metrics agree
    within 0.5 dB."""
    src_gen = GEN / "speech.wav"
    x = _mono(src_gen)
    scaled = _write(tmp_path / "scaled.wav", (x * 10 ** (-20 / 20)).astype(np.float32))
    outs = []
    for key, src in (("a", src_gen), ("b", scaled)):
        (tmp_path / key).mkdir()
        enh = _enhance(src, tmp_path / key)
        loud = IMPLEMENTATIONS["loudness"](
            enh, {"target": "podcast"}, _ctx(tmp_path / key, enh)).path
        outs.append(_mono(loud))
    ya, yb = outs
    assert abs(_deep_floor_db(ya, SR) - _deep_floor_db(yb, SR)) <= 0.5
    ma, mb = level_metrics(ya, SR), level_metrics(yb, SR)
    assert abs(ma["speech_dbfs"] - mb["speech_dbfs"]) <= 0.5
    assert abs(
        speech_band_ratio_db(ya, SR) - speech_band_ratio_db(yb, SR)
    ) <= 0.5
    sa = quiet_band_share_db(ya, SR, 80, 200)
    sb = quiet_band_share_db(yb, SR, 80, 200)
    if np.isfinite(sa) or np.isfinite(sb):
        assert abs(sa - sb) <= 0.5, f"quiet share {sa:.2f} vs {sb:.2f}"


def test_expander_keeps_word_endings(tmp_path, real_dir):
    """Review Focus 3: pink-noise floor at −45 dBFS — speech-band energy in
    the last 80 ms of each VAD segment drops ≤ 2 dB after enhance."""
    from tests.audio_metrics import band_rms_db, load_mono

    real = sorted(real_dir.glob("*.m4a"))[0]
    src = decode_to_wav(real, tmp_path / "dec.wav",
                        probe(real).audio[0].index).path
    x, sr = load_mono(src)
    pink = _pink_noise(len(x), sr)
    pink *= (10 ** (-45 / 20)) / np.sqrt((pink ** 2).mean())
    noisy = (x + pink).astype(np.float32)
    nin = _write(tmp_path / "noisy.wav", noisy)
    res = _enhance(nin, tmp_path)
    y, _ = load_mono(res)
    segs = _vad_segments(x, sr)
    assert segs, "VAD found no speech in the real recording"
    tail = int(0.080 * sr)
    drops = []
    for a, b in segs:
        if b - a < 2 * tail:
            continue
        # vs the CLEAN tail: the injected −45 dB floor is *supposed* to be
        # gated out — counting it as a "drop" would measure the gate doing
        # its job, not chopped word endings.
        before = band_rms_db(x[b - tail:b], sr, 300, 3000)
        after = band_rms_db(y[b - tail:b], sr, 300, 3000)
        drops.append(before - after)
    assert drops, "no segments long enough for the 80 ms tail test"
    worst = max(drops)
    assert worst <= 2.0, f"word ending chopped by {worst:.1f} dB"


def _m4a_chain(in_wav: Path, tmp_path: Path) -> Path:
    """Reference: the M4a enhance chain (static thresholds, +2 dB makeup,
    80 Hz HPF, no expander/boom EQ) + deesser, for before/after numbers."""
    from pedalboard import (
        Compressor, Gain, HighShelfFilter, HighpassFilter, PeakFilter,
        Pedalboard,
    )
    from pedalboard.io import AudioFile

    from backend.media.ffmpeg_run import run_filter

    board = Pedalboard([
        HighpassFilter(cutoff_frequency_hz=80),
        PeakFilter(cutoff_frequency_hz=250, gain_db=-2, q=1.0),
        PeakFilter(cutoff_frequency_hz=3500, gain_db=3, q=1.0),
        HighShelfFilter(cutoff_frequency_hz=10000, gain_db=1.5),
        Compressor(threshold_db=-18, ratio=3, attack_ms=5, release_ms=80),
        Gain(gain_db=2),
    ])
    tmp = tmp_path / "m4a_ped.wav"
    with AudioFile(str(in_wav)) as f, \
            AudioFile(str(tmp), "w", int(f.samplerate), f.num_channels) as o:
        done = 0
        while done < f.frames:
            data = f.read(min(int(f.samplerate * 10), f.frames - done))
            done += data.shape[-1]
            o.write(board.process(data, int(f.samplerate), reset=False))
    out = tmp_path / "m4a_out.wav"
    run_filter(tmp, out, "deesser=i=0.4:m=0.5:f=0.5",
               _ctx(tmp_path, in_wav))
    return out


def test_enhance_real_recording_metric_targets(tmp_path, real_dir):
    """After enhance(voice) + loudness(podcast): floor ≤ −66 dBFS,
    speech-to-floor ≥ 50 dB, quiet 80–200 Hz share ≤ −11 dB, boom ratio
    down 3–5 dB vs M4a, −16 ±0.5 LUFS, TP ≤ −1 dBTP (plan Task 2 tests).

    The absolute floor/S/F/share targets only bind when the input actually
    has a quiet floor to recover: a 2.5:1 expander can cut at most
    ``(floor − T) × 1.5`` dB, so a recording whose noise bed sits ≥ ~25 dB
    under its speech (floor+10 > speech−18 → the clamp caps T) cannot reach
    −66 — that floor needs the M6 denoiser. On such recordings the test
    instead asserts the improvement vs the M4a chain on the same file."""
    from backend.media.loudness import (
        integrated_lufs, level_stats, true_peak_db,
    )
    from tests.audio_metrics import load_mono

    for real in sorted(real_dir.glob("*.m4a")):
        src = decode_to_wav(real, tmp_path / f"dec_{real.stem}.wav",
                            probe(real).audio[0].index).path
        st = level_stats(src)
        quiet_floor = st["floor_dbfs"] <= st["speech_dbfs"] - 25.0
        old_dir = tmp_path / f"{real.stem}_m4a"
        new_dir = tmp_path / f"{real.stem}_m4c"
        old_dir.mkdir(exist_ok=True)
        new_dir.mkdir(exist_ok=True)
        m4a_out = _m4a_chain(src, old_dir)
        enh = _enhance(src, new_dir, deess=True, reduce_boom=True)
        res = IMPLEMENTATIONS["loudness"](
            enh, {"target": "podcast"}, _ctx(new_dir, enh)).path
        m4a_loud = IMPLEMENTATIONS["loudness"](
            m4a_out, {"target": "podcast"}, _ctx(old_dir, m4a_out)).path

        x, sr = load_mono(res)
        m = level_metrics(x, sr)
        share = quiet_band_share_db(x, sr, 80, 200)
        boom_new = speech_band_ratio_db(x, sr)
        xo, _ = load_mono(m4a_loud)
        m_old = level_metrics(xo, sr)
        share_old = quiet_band_share_db(xo, sr, 80, 200)
        boom_old = speech_band_ratio_db(xo, sr)
        lufs = integrated_lufs(res)
        tp = true_peak_db(res)
        print(f"\n{real.name}: floor {m['floor_dbfs']:.1f} dBFS | "
              f"S/F {m['speech_to_floor_db']:.1f} dB | "
              f"quiet 80-200 {share:.1f} dB | "
              f"boom {boom_old:.1f}->{boom_new:.1f} dB | "
              f"{lufs:.1f} LUFS | TP {tp:.2f}")
        if quiet_floor:
            assert m["floor_dbfs"] <= -66.0
            assert m["speech_to_floor_db"] >= 50.0
            assert share <= -11.0
        else:
            # Noisy-floor recording: the gate can't mathematically reach
            # the absolute targets — assert real improvement vs M4a.
            assert m["floor_dbfs"] <= m_old["floor_dbfs"] - 2.0
            assert m["speech_to_floor_db"] >= m_old["speech_to_floor_db"] + 2.0
            assert share <= share_old - 2.0
        assert -5.0 <= boom_new - boom_old <= -3.0
        assert lufs == pytest.approx(-16.0, abs=0.5)
        assert tp <= -1.0
