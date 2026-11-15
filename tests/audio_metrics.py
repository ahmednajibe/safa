"""Shared audio metrics for M4c review-focus tests.

The level/band-share helpers are ported from the orchestrator's
``.orchestrator/analysis/compare.py`` (50 ms frame-RMS floor/speech
percentiles, quiet-part band shares) so the numbers here are directly
comparable to the reported before/after measurements. The consonant-window
detector deliberately mirrors the production detector's *idea* on an
independent implementation, so a bug in one doesn't mask the other.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf
from scipy import ndimage, signal


def load_mono(path: Path | str) -> tuple[np.ndarray, int]:
    x, sr = sf.read(str(path), dtype="float32", always_2d=True)
    return x.mean(axis=1), int(sr)


def _frames(x: np.ndarray, sr: int, ms: float = 50.0) -> np.ndarray:
    n = max(1, int(sr * ms / 1000))
    k = len(x) // n
    return x[: k * n].reshape(k, n)


def _db(v: np.ndarray) -> np.ndarray:
    return 20 * np.log10(np.maximum(v, 1e-9))


# ---- level metrics (compare.py port) ----------------------------------------


def level_metrics(x: np.ndarray, sr: int) -> dict:
    """floor = mean of quietest 10 % of 50 ms frames; speech = loudest 33 %."""
    f = _frames(x, sr)
    rms = np.sqrt((f ** 2).mean(axis=1))
    r = np.sort(_db(rms))
    floor = float(r[: max(1, len(r) // 10)].mean())
    speech = float(r[-max(1, len(r) // 3):].mean())
    return {
        "floor_dbfs": floor,
        "speech_dbfs": speech,
        "speech_to_floor_db": speech - floor,
    }


def bandpass(x: np.ndarray, sr: int, lo: float, hi: float) -> np.ndarray:
    sos = signal.butter(4, [lo, hi], btype="band", fs=sr, output="sos")
    return signal.sosfiltfilt(sos, x)


def band_rms_db(x: np.ndarray, sr: int, lo: float, hi: float) -> float:
    b = bandpass(x, sr, lo, hi)
    return float(_db(np.sqrt((b ** 2).mean() + 1e-20)))


def quiet_band_share_db(
    x: np.ndarray, sr: int, lo: float, hi: float,
    frac: float = 0.1, frame_ms: float = 50.0,
) -> float:
    """Band share of total energy inside the quietest ``frac`` of frames
    (compare.py's QUIET-parts band share)."""
    f = _frames(x, sr, frame_ms)
    rms = np.sqrt((f ** 2).mean(axis=1))
    q = f[np.argsort(rms)[: max(1, int(len(rms) * frac))]].ravel()
    if len(q) < 16:
        return -np.inf
    spec = np.abs(np.fft.rfft(q * np.hanning(len(q)))) ** 2
    fr = np.fft.rfftfreq(len(q), 1 / sr)
    tot = spec.sum()
    if tot <= 0:
        return -np.inf
    return float(10 * np.log10(spec[(fr >= lo) & (fr < hi)].sum() / tot))


def srmr_proxy(x: np.ndarray, sr: int) -> float:
    """SRMR proxy (try_wpe.py port): 2-8 Hz vs 16-40 Hz envelope-modulation
    energy of the 300-4000 Hz band, in dB. Reverberation smears the
    envelope and shifts energy to fast modulation — lower is more
    reverberant."""
    b = signal.butter(4, [300, 4000], btype="band", fs=sr, output="sos")
    v = signal.sosfiltfilt(b, x)
    n = int(sr * 0.01)
    k = len(v) // n
    if k == 0:
        return 0.0
    env = 10 * np.log10((v[: k * n].reshape(k, n) ** 2).mean(1) + 1e-12)
    fe, Pe = signal.welch(env - env.mean(), fs=100, nperseg=256)
    lo = Pe[(fe >= 2) & (fe <= 8)].sum()
    hi = Pe[(fe >= 16) & (fe <= 40)].sum()
    return float(10 * np.log10(lo / (hi + 1e-20)))


def ltas_ripple_db(
    x: np.ndarray, sr: int, fmin: float = 220.0, fmax: float = 1000.0
) -> float:
    """RMS ripple of the long-term spectrum over its median baseline in
    [fmin,fmax] Hz (try_wpe.py port) — comb/resonance structure raises it."""
    f, P = signal.welch(x, fs=sr, nperseg=4096)
    db = 10 * np.log10(P + 1e-20)
    band = (f >= fmin) & (f <= fmax)
    resid = db - ndimage.median_filter(db, size=15)
    return float(np.sqrt((resid[band] ** 2).mean()))


def speech_band_ratio_db(
    x: np.ndarray, sr: int,
    band_lo: tuple[float, float] = (80.0, 200.0),
    band_hi: tuple[float, float] = (300.0, 3000.0),
) -> float:
    """band_lo/band_hi energy ratio over the loudest-third 50 ms frames —
    the "voiced boom" metric (a large ratio = boomy voice)."""
    f = _frames(x, sr)
    rms = np.sqrt((f ** 2).mean(axis=1))
    top = f[np.argsort(rms)[-max(1, len(rms) // 3):]].ravel()
    spec = np.abs(np.fft.rfft(top * np.hanning(len(top)))) ** 2
    fr = np.fft.rfftfreq(len(top), 1 / sr)
    e_lo = spec[(fr >= band_lo[0]) & (fr < band_lo[1])].sum()
    e_hi = spec[(fr >= band_hi[0]) & (fr < band_hi[1])].sum()
    return float(10 * np.log10((e_lo + 1e-20) / (e_hi + 1e-20)))


# ---- envelopes / consonant windows ------------------------------------------


def envelope_db(
    x: np.ndarray, sr: int, win_ms: float = 1.5, hop_ms: float = 1.0
) -> tuple[np.ndarray, int, int]:
    """Short-window RMS envelope in dB at ``hop_ms`` hops."""
    win = max(1, int(sr * win_ms / 1000))
    hop = max(1, int(sr * hop_ms / 1000))
    if len(x) < win:
        return np.zeros(0), win, hop
    n = 1 + (len(x) - win) // hop
    idx = np.arange(win)[None, :] + hop * np.arange(n)[:, None]
    rms = np.sqrt((x[idx] ** 2).mean(axis=1) + 1e-18)
    return _db(rms), win, hop


def consonant_windows(
    x: np.ndarray, sr: int,
    min_ms: float = 12.0, max_ms: float = 80.0, over_db: float = 4.0,
    abs_db: float = -55.0,
) -> list[tuple[int, int]]:
    """12–80 ms bursts where the 2.5–12 kHz envelope stays ``over_db`` above
    its 40 ms local median and peaks above ``abs_db`` — the plosive/fricative
    zones a declicker must not touch (Review Focus 1). Quieter than
    ``abs_db`` there is no consonant to protect (noise ripples also sit many
    dB over a near-silence median). Returns sample spans."""
    hi = bandpass(x, sr, 2500.0, 12000.0)
    env, win, hop = envelope_db(hi, sr)
    if len(env) < 41:
        return []
    med = ndimage.median_filter(env, size=41, mode="nearest")
    mask = ndimage.binary_closing(
        env > med + over_db, structure=np.ones(3, bool)
    )
    d = np.diff(mask.astype(np.int8), prepend=0, append=0)
    starts, ends = np.where(d == 1)[0], np.where(d == -1)[0]
    out = []
    for a, b in zip(starts, ends):
        dur_ms = (b - a) * hop / sr * 1000
        if min_ms <= dur_ms <= max_ms and env[a:b].max() > abs_db:
            out.append((int(a * hop), int(min(len(x), b * hop + win))))
    return out


def window_band_rms_db(
    x: np.ndarray, sr: int, windows: list[tuple[int, int]],
    lo: float, hi: float,
) -> list[float]:
    """Per-window RMS (dBFS) of the [lo,hi] band inside each sample span."""
    band = bandpass(x, sr, lo, hi)
    out = []
    for a, b in windows:
        seg = band[a:b]
        if len(seg) == 0:
            out.append(-np.inf)
        else:
            out.append(float(_db(np.sqrt((seg ** 2).mean() + 1e-20))))
    return out
