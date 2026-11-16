"""Mouth-click detection + band-limited repair (M4c Task 1, M4d rewrite).

Saliva clicks and lip smacks live almost entirely above ~2.5 kHz and
last a fraction of a millisecond to ~3.5 ms. M4d retargets the failure
modes the M4c detector showed on real speech (Argon/Astra second
round): mid-band vetoes rejected clicks whose mid band was simply
modulated by the surrounding vowel, MAD self-masking hid clicks inside
speech bursts, and the unconstrained least-squares AR repair once
amplified a click by +11 dB.

Detector — everything happens on the complementary >2.5 kHz band:

- Short STFT: ``n_fft`` 256, Hann window 128 (2.67 ms), hop 32 (0.67 ms).
  Per-bin baseline = temporal median over ±15 ms computed in two passes
  (clip at median + 6 dB, re-median) so bursts can't raise their own
  floor — the ±15 ms window doubles as the harmonic context: a
  sustained harmonic is exactly what the median describes.
- MAD per bin capped at 3 dB; a bin-frame is flagged when its excess
  over ``baseline + 1.4826·MAD`` reaches the threshold (normal 7 dB,
  gentle 9, strong 5, residual pass 8).
- Consonant protection by persistence: binary opening of the flag mask
  with ≥6 ms per-bin persistence deletes fricative/aspiration tails
  (t/k/p/s/sh/ch hold −12 dB for 9–30 ms) without touching 0.7–4 ms
  clicks. The old mid-band vetoes are gone.
- A candidate needs ≥30 % of the 4–14 kHz bins flagged inside the same
  0.5–3.5 ms event (event duration measured at −12 dB from the band
  energy peak).
- Word-initial plosive veto, narrow: the <2.5 kHz envelope jumps >10 dB
  within 2 ms after ≥15 ms below −50 dBFS AND a >8 ms high-band hold
  follows — the silence→burst→hold shape of a plosive release.
- GCI comb veto: high-band-poor (4.5–14 kHz energy >25 dB under the
  0.5–2.5 kHz band) candidates that repeat at the pitch period
  (6–12 ms, ±2.5 dB) are glottal ticks, not clicks.
- Bursts: events whose centres are ≤50 ms apart form one group; each
  tick is still detected and repaired individually.

Repair — the <2.5 kHz band is never touched (``low`` passes through
bit-identically):

- isolated ticks (≤ ~3 ms, outside speech): high-band time-domain fill
  over ``[c0−0.5 ms, c1+1.5 ms]`` by bidirectional **Burg** AR
  prediction, order 24, reflection coefficients clipped to |k| < 0.995
  (guaranteed stable — the M4c least-squares fit is what could blow
  up), fitted on unflagged samples only.
- bursts, spans >3 ms, in-speech ticks and every residual-mode repair:
  per-bin STFT **magnitude clamping** to the local median on flagged
  bin-frames, original phase kept, gain mask smoothed over 3 bins.
- ≥2 ms raised-cosine crossfades at every patch edge.
- Hard guard per span: if the repair's high-band energy would rise,
  that span reverts to the original samples.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import ndimage, signal

try:
    import numba

    _HAVE_NUMBA = True
except ImportError:  # pragma: no cover - numba ships via librosa/resampy
    _HAVE_NUMBA = False

_SPLIT_HZ = 2500.0   # complementary band split
_MID_BAND = (500.0, 2500.0)

# STFT: n_fft 256, Hann 128 (2.67 ms @48 kHz), hop 32 (0.67 ms).
_STFT_NFFT = 256
_STFT_NPERSEG = 128
_STFT_HOP = 32

_MED_FRAMES = 45      # ±15 ms baseline window (30 ms total)
_BASE_CLIP_DB = 6.0   # two-stage baseline: clip input at median+6 first
_MAD_CAP_DB = 3.0
_MAD_SIGMA = 1.4826
_PERSIST_FRAMES = 9   # ≥6 ms per-bin flag persistence → consonant

_EVENT_BAND = (4000.0, 14000.0)  # bins counted for the ≥30% rule
_FLAG_FRAC = 0.30
_W12_MAX_MS = 3.5     # −12 dB event width ceiling
_EVENT_MIN_MS = 0.5
_PEAK_MIN_DB = -70.0  # events whose 4–14 kHz peak is below this are
                       # inaudible (noise-floor wiggles, codec hiss)

_PLOS_QUIET_MS = 15.0   # word-initial plosive veto: …
_PLOS_QUIET_DB = -50.0  #   ≥15 ms below −50 dBFS in the low band …
_PLOS_JUMP_DB = 10.0    #   … then a >10 dB low-band jump within 2 ms …
_PLOS_JUMP_MS = 2.0
_PLOS_HOLD_MS = 8.0     #   … and a >8 ms high-band hold afterwards.
_PLOS_HOLD_DB = 15.0

_GCI_HI = (4500.0, 14000.0)
_GCI_LO = (500.0, 2500.0)
_GCI_DELTA_DB = 25.0    # high-band-poor by more than this …
_GCI_T0_FRAMES = (9, 18)  # … and repeating at 6–12 ms (±2.5 dB) → glottal
_GCI_MATCH_DB = 2.5

_BURST_GAP_S = 0.05

_AR_ORDER = 24
_AR_K_MAX = 0.995
_AR_CTX_MS = 10.0
_AR_MIN_CTX_MS = 2.0
_FILL_PRE_MS = 0.5    # tick fill starts 0.5 ms before the span …
_FILL_POST_MS = 1.5   # … and ends 1.5 ms after it
_XFADE_MS = 2.0
_TICK_MAX_MS = 4.5    # span incl. STFT window that still counts as a tick
_INSPEECH_DBFS = -45.0  # mid-band level above this → in-speech → clamp
_CLAMP_EXCESS_DB = 3.0  # gain only where per-bin excess > 3 dB
_CLAMP_CTX_MS = 50.0    # baseline context for the STFT clamp
_CLAMP_WRITE_MS = 4.0   # write region beyond the span


# ---- band split --------------------------------------------------------------


def split_bands(
    x: np.ndarray, sr: int, cutoff: float = _SPLIT_HZ
) -> tuple[np.ndarray, np.ndarray]:
    """Complementary low/high split at ``cutoff`` Hz.

    ``low + high == x`` exactly — the high band is ``x - low``, so
    reconstruction error is the float residual, far below −80 dB.
    """
    x = np.asarray(x, dtype=np.float64)
    if len(x) < 64:
        return x.copy(), np.zeros_like(x)
    sos = signal.butter(4, cutoff, btype="low", fs=sr, output="sos")
    low = signal.sosfiltfilt(sos, x)
    return low, x - low


def _band(x: np.ndarray, sr: int, lo: float, hi: float) -> np.ndarray:
    hi = min(hi, sr * 0.45)
    sos = signal.butter(4, [lo, hi], btype="band", fs=sr, output="sos")
    return signal.sosfiltfilt(sos, x)


def _env_db(x: np.ndarray, sr: int, win_ms: float, hop_ms: float):
    """Short RMS envelope in dB; frame i covers [i*hop, i*hop+win)."""
    win = max(1, int(sr * win_ms / 1000))
    hop = max(1, int(sr * hop_ms / 1000))
    if len(x) < win:
        return np.zeros(0), win, hop
    n = 1 + (len(x) - win) // hop
    idx = hop * np.arange(n)[:, None] + np.arange(win)[None, :]
    rms = np.sqrt((x[idx] ** 2).mean(axis=1) + 1e-18)
    return 20.0 * np.log10(np.maximum(rms, 1e-9)), win, hop


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """[start, end) runs of True in a 1-D bool mask."""
    d = np.diff(mask.astype(np.int8), prepend=0, append=0)
    starts, ends = np.where(d == 1)[0], np.where(d == -1)[0]
    return list(zip(starts.tolist(), ends.tolist()))


# ---- STFT analysis -----------------------------------------------------------


def _stft(x: np.ndarray, sr: int):
    """(freqs, times_s, Z) — frame k is centred near sample k*hop."""
    f, t, Z = signal.stft(
        x, fs=sr, window="hann", nperseg=_STFT_NPERSEG,
        noverlap=_STFT_NPERSEG - _STFT_HOP, nfft=_STFT_NFFT,
        boundary="zeros", padded=True,
    )
    return f, t, Z


if _HAVE_NUMBA:

    @numba.njit
    def _median_row_nb(row: np.ndarray, out_r: np.ndarray, w: int) -> None:
        """One row's running median, odd window ``w``, edge-replicated —
        sorted-window slide, O(log w) per step."""
        n = row.shape[0]
        h = w // 2
        win = np.empty(w)
        for k in range(w):
            idx = k - h
            if idx < 0:
                idx = 0
            if idx >= n:
                idx = n - 1
            win[k] = row[idx]
        win.sort()
        out_r[0] = win[h]
        for i in range(1, n):
            old = row[i - 1 - h] if i - 1 - h >= 0 else row[0]
            new = row[i + h] if i + h < n else row[n - 1]
            j = -1
            lo, hi2 = 0, w - 1
            while lo <= hi2:
                mid = (lo + hi2) // 2
                if win[mid] == old:
                    j = mid
                    break
                elif win[mid] < old:
                    lo = mid + 1
                else:
                    hi2 = mid - 1
            while j > 0 and win[j - 1] == old:
                j -= 1
            if j < 0:
                for k in range(w):
                    if win[k] == old:
                        j = k
                        break
            for k in range(j, w - 1):
                win[k] = win[k + 1]
            k = w - 2
            if new >= win[w - 2]:
                win[w - 1] = new
            else:
                while k >= 0 and win[k] > new:
                    win[k + 1] = win[k]
                    k -= 1
                win[k + 1] = new
            out_r[i] = win[h]

    @numba.njit(parallel=True)
    def _median_rows_nb_par(a: np.ndarray, w: int) -> np.ndarray:
        out = np.empty_like(a)
        for r in numba.prange(a.shape[0]):
            _median_row_nb(a[r], out[r], w)
        return out

    @numba.njit
    def _median_rows_nb_seq(a: np.ndarray, w: int) -> np.ndarray:
        out = np.empty_like(a)
        for r in range(a.shape[0]):
            _median_row_nb(a[r], out[r], w)
        return out


# Thread-pool dispatch costs ~4 ms per call — for the small per-span
# clamp windows the serial kernel wins outright.
_MEDIAN_PAR_MIN = 1_000_000


def _median_rows(a: np.ndarray, w: int) -> np.ndarray:
    """Per-bin median along the time axis, odd window ``w``,
    edge-replicated — the same element ``ndimage.median_filter(a,
    (1, w), "nearest")`` picks (verified bit-identical), ~30× faster
    than the scipy rank filter (R2 #7)."""
    a = np.ascontiguousarray(a)
    if _HAVE_NUMBA and w % 2 == 1 and a.shape[1] > 0:
        try:
            if a.size >= _MEDIAN_PAR_MIN:
                return _median_rows_nb_par(a, w)
            return _median_rows_nb_seq(a, w)
        except Exception:  # pragma: no cover - JIT failure → scipy
            pass
    return ndimage.median_filter(a, size=(1, w), mode="nearest")


def _baseline(db: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Per-bin (baseline_dB, excess) — two-stage temporal median + capped
    MAD. The clip-then-re-median keeps bursts from raising their own
    floor (MAD self-masking was M4c's biggest miss)."""
    med1 = _median_rows(db, _MED_FRAMES)
    clipped = np.minimum(db, med1 + _BASE_CLIP_DB)
    base = _median_rows(clipped, _MED_FRAMES)
    mad = _median_rows(np.abs(db - base), _MED_FRAMES)
    floor = base + _MAD_SIGMA * np.minimum(mad, _MAD_CAP_DB)
    return floor, db - floor


@dataclass
class ClickEvent:
    """One detected click/burst tick (samples + detector metadata)."""

    start: int
    end: int
    score_db: float   # peak band-frame excess over the local baseline
    burst: bool = False


def _band_profile(mag: np.ndarray, freqs: np.ndarray) -> np.ndarray:
    """4–14 kHz summed energy per frame, in dB — the event profile used
    for the −12 dB width and the GCI repeat test."""
    e_bins = (freqs >= _EVENT_BAND[0]) & (freqs <= _EVENT_BAND[1])
    e = (mag[e_bins] ** 2).sum(axis=0)
    return 10.0 * np.log10(np.maximum(e, 1e-18))


def _plosive_veto(
    low_env: np.ndarray,
    hi_env: np.ndarray,
    env_hop: int,
    sr: int,
    t0_samp: int,
    peak_db: float,
) -> bool:
    """silence → low-band jump → high-band hold = word-initial plosive."""
    hop_s = env_hop / sr
    i0 = int(round(t0_samp / env_hop))
    pre_a = int(round((t0_samp / sr - _PLOS_QUIET_MS / 1000 - 0.002) / hop_s))
    pre_b = int(round((t0_samp / sr - 0.002) / hop_s))
    if pre_a < 0 or pre_b <= pre_a or i0 >= len(low_env):
        return False
    if not (low_env[pre_a:pre_b] < _PLOS_QUIET_DB).all():
        return False
    j0 = max(0, i0 - int(round(0.001 / hop_s)))
    j1 = min(len(low_env), i0 + int(round(_PLOS_JUMP_MS / 1000 / hop_s)) + 1)
    if float(low_env[j0:j1].max() - low_env[pre_a:pre_b].max()) <= _PLOS_JUMP_DB:
        return False
    # >8 ms high-band hold within 15 dB of the envelope's own peak
    # (band_db and the RMS envelope are different scales — compare the
    # hold to the envelope peak, not the STFT sum-energy peak).
    hi_pk = float(hi_env[max(0, i0 - 2): i0 + 4].max())
    hold = 0.0
    k = i0
    lim = min(len(hi_env), i0 + int(round(0.015 / hop_s)))
    while k < lim and hi_env[k] >= hi_pk - _PLOS_HOLD_DB:
        hold += hop_s
        k += 1
    return hold * 1000.0 > _PLOS_HOLD_MS


def _gci_veto(
    band_db: np.ndarray,
    e_lo: np.ndarray,
    e_hi: np.ndarray,
    pk_frame: int,
) -> bool:
    """High-band-poor events repeating at the pitch period are glottal
    pulses (GCI comb), not clicks."""
    if pk_frame >= len(e_lo) or pk_frame >= len(e_hi):
        return False
    if float(e_hi[pk_frame]) >= float(e_lo[pk_frame]) - _GCI_DELTA_DB:
        return False
    pk = float(band_db[pk_frame])
    lo, hi = _GCI_T0_FRAMES
    for t0 in range(lo, hi + 1):
        for q in (pk_frame - t0, pk_frame + t0):
            if 0 <= q < len(band_db) and abs(float(band_db[q]) - pk) <= _GCI_MATCH_DB:
                return True
    return False


def detect_events(
    x: np.ndarray, sr: int, z_thresh: float = 7.0
) -> list[ClickEvent]:
    """Detected clicks with score/burst metadata (audit + repair)."""
    x = np.asarray(x, dtype=np.float64)
    if len(x) < int(sr * 0.05):
        return []
    low, high = split_bands(x, sr)
    freqs, _t, Z = _stft(high, sr)
    mag = np.abs(Z)
    db = 20.0 * np.log10(np.maximum(mag, 1e-9))
    floor, excess = _baseline(db)

    flagged = excess >= float(z_thresh)
    persistent = ndimage.binary_opening(
        flagged, structure=np.ones((1, _PERSIST_FRAMES), bool)
    )
    transient = flagged & ~persistent

    e_bins = (freqs >= _EVENT_BAND[0]) & (freqs <= _EVENT_BAND[1])
    n_eligible = int(e_bins.sum())
    if n_eligible == 0 or not transient.any():
        return []
    frac = transient[e_bins].mean(axis=0)
    hot = ndimage.binary_closing(frac >= _FLAG_FRAC, structure=np.ones(3))
    runs = _runs(hot)
    if not runs:
        return []

    band_db = _band_profile(mag, freqs)
    # GCI-veto band energies are measured on the FULL signal — the
    # high-band-only STFT can't see the 0.5–2.5 kHz dominance that marks
    # glottal pulses. Env hop matches the STFT hop (32 samples).
    e_lo, _, _ = _env_db(
        _band(x, sr, *_GCI_LO), sr,
        _STFT_NPERSEG / sr * 1000.0, _STFT_HOP / sr * 1000.0,
    )
    e_hi, _, _ = _env_db(
        _band(x, sr, *_GCI_HI), sr,
        _STFT_NPERSEG / sr * 1000.0, _STFT_HOP / sr * 1000.0,
    )
    low_env, _ew, eh = _env_db(low, sr, 1.5, 1.0)
    hi_env, _, _ = _env_db(high, sr, 1.5, 1.0)

    hop_ms = _STFT_HOP / sr * 1000.0
    events: list[ClickEvent] = []
    for a, b in runs:
        pk_off = int(np.argmax(band_db[a:b]))
        p = a + pk_off
        pk = float(band_db[p])
        # −12 dB width about the peak, but never wider than the local
        # speech floor allows: in loud sustained speech the profile can't
        # drop 12 dB, so measure the impulse's own width above
        # max(pk−12, local_median+3).
        cl, cr = max(0, p - 60), min(len(band_db), p + 60)
        ctx = np.r_[band_db[cl:p - 2], band_db[p + 3:cr]] if p - 2 > cl \
            else band_db[p + 3:cr]
        med = float(np.median(ctx)) if len(ctx) else -np.inf
        thresh = max(pk - 12.0, med + 3.0)
        l = p
        while l > 0 and band_db[l - 1] >= thresh:
            l -= 1
        r = p
        while r + 1 < len(band_db) and band_db[r + 1] >= thresh:
            r += 1
        if not (_EVENT_MIN_MS <= (r - l) * hop_ms <= _W12_MAX_MS):
            continue
        if pk < _PEAK_MIN_DB:
            continue
        if _plosive_veto(low_env, hi_env, eh, sr, l * _STFT_HOP, pk):
            continue
        if _gci_veto(band_db, e_lo, e_hi, p):
            continue
        score = float(excess[e_bins][:, a:b].max())
        events.append(
            ClickEvent(
                start=int(l * _STFT_HOP),
                end=int(min(len(x), r * _STFT_HOP + _STFT_NPERSEG)),
                score_db=score,
            )
        )
    # Burst grouping: centres ≤50 ms apart.
    centres = [(e.start + e.end) / 2.0 for e in events]
    gap = _BURST_GAP_S * sr
    for i, e in enumerate(events):
        e.burst = any(
            abs(centres[i] - centres[j]) <= gap
            for j in range(len(events))
            if j != i
        )
    return events


def detect_mouth_clicks(
    x: np.ndarray, sr: int, z_thresh: float = 7.0
) -> list[tuple[int, int]]:
    """Detect mouth clicks in mono ``x``; returns sample spans.

    ``z_thresh`` is the per-bin-frame excess threshold in dB
    (plan: gentle 9, normal 7, strong 5, residual pass 8).
    """
    return [(e.start, e.end) for e in detect_events(x, sr, z_thresh)]


# ---- repair ------------------------------------------------------------------


def _burg_coefs(x: np.ndarray, order: int) -> np.ndarray:
    """Burg AR coefficients a[0..order] (a[0]=1; x[n] ≈ −Σ a[k] x[n−k]).

    Reflection coefficients are clipped to |k| < 0.995 at every
    recursion step, which guarantees a stable minimum-phase fit — the
    M4c unconstrained least-squares fit is what once amplified a click
    by +11 dB.
    """
    x = np.asarray(x, dtype=np.float64)
    n = len(x)
    ef = x.copy()
    eb = x.copy()
    a = np.zeros(order + 1)
    a[0] = 1.0
    for m in range(1, order + 1):
        ff = ef[m:].copy()
        bb = eb[m - 1: n - 1].copy()
        den = float(ff @ ff + bb @ bb)
        k = 0.0 if den <= 1e-30 else float(-2.0 * (bb @ ff) / den)
        k = float(np.clip(k, -_AR_K_MAX, _AR_K_MAX))
        a_new = a.copy()
        if m > 1:
            a_new[1:m] = a[1:m] + k * a[m - 1:0:-1]
        a_new[m] = k
        a = a_new
        ef[m:] = ff + k * bb
        eb[m:] = bb + k * ff
    return a


def _ar_predict(ctx: np.ndarray, order: int, n_out: int) -> np.ndarray | None:
    """Iterate the Burg fit ``n_out`` samples forward from ``ctx``."""
    if len(ctx) < order * 2 or len(ctx) - order <= order:
        return None
    coefs = _burg_coefs(ctx, order)
    buf = np.empty(order + n_out)
    buf[:order] = ctx[-order:]
    clip = 4.0 * float(np.abs(ctx).max()) + 1e-6
    for i in range(n_out):
        v = -float(coefs[1:] @ buf[i: i + order][::-1])
        buf[order + i] = np.clip(v, -clip, clip)
    out = buf[order:]
    return out if np.isfinite(out).all() else None


def _ar_fill(
    high: np.ndarray, ra: int, rb: int, flagged: np.ndarray, sr: int
) -> np.ndarray | None:
    """Bidirectional Burg AR fill, blended with a raised-cosine weight."""
    order = _AR_ORDER
    ctx = int(_AR_CTX_MS * sr / 1000)
    min_ctx = max(order + 8, int(_AR_MIN_CTX_MS * sr / 1000))
    li = np.arange(max(0, ra - ctx), ra)
    ri = np.arange(rb, min(len(high), rb + ctx))
    lctx = high[li[~flagged[li]]]
    rctx = high[ri[~flagged[ri]]]
    n = rb - ra
    fwd = _ar_predict(lctx, order, n) if len(lctx) >= min_ctx else None
    bwd = (
        _ar_predict(rctx[::-1], order, n)[::-1]
        if len(rctx) >= min_ctx
        else None
    )
    if fwd is None:
        return bwd
    if bwd is None:
        return fwd
    w = 0.5 + 0.5 * np.cos(np.pi * (np.arange(n) + 0.5) / n)
    return w * fwd + (1.0 - w) * bwd


def _stft_clamp(
    high: np.ndarray, pa: int, pb: int, sr: int
) -> np.ndarray | None:
    """Magnitude-clamp flagged bin-frames over [pa,pb) to the local
    median, phase kept. Returns the patched region or None."""
    w0 = max(0, pa - int(_CLAMP_CTX_MS * sr / 1000))
    w1 = min(len(high), pb + int(_CLAMP_CTX_MS * sr / 1000))
    seg = high[w0:w1]
    if len(seg) < _STFT_NPERSEG:
        return None
    freqs, _t, Z = _stft(seg, sr)
    mag = np.abs(Z)
    db = 20.0 * np.log10(np.maximum(mag, 1e-9))
    floor, excess = _baseline(db)
    n_f = Z.shape[1]
    # Only frames whose centre lies inside the write region are clamped —
    # context frames exist purely to stabilise the baseline.
    f0 = (pa - w0) // _STFT_HOP
    f1 = min(n_f, (pb - w0) // _STFT_HOP + 2)
    gain = np.ones_like(mag)
    need = (excess > _CLAMP_EXCESS_DB)
    need[:, :f0] = False
    need[:, f1:] = False
    target_mag = 10.0 ** (floor / 20.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        g = np.where(need, np.minimum(1.0, target_mag / np.maximum(mag, 1e-18)), 1.0)
    gain = ndimage.uniform_filter(g, size=(3, 1), mode="nearest")
    Z2 = Z * gain
    _tt, y = signal.istft(
        Z2, fs=sr, window="hann", nperseg=_STFT_NPERSEG,
        noverlap=_STFT_NPERSEG - _STFT_HOP, nfft=_STFT_NFFT,
        boundary=True,
    )
    s0, s1 = pa - w0, min(len(y), pb - w0)
    if s1 <= s0 or s0 < 0:
        return None
    return y[s0:s1]


def repair_mouth_clicks(
    x: np.ndarray,
    sr: int,
    spans: list[tuple[int, int]],
    *,
    mode: str = "normal",
) -> np.ndarray:
    """Repair ``spans`` in mono ``x``; same length out, float64.

    The <2.5 kHz band and everything outside the flagged spans is
    bit-identical. ``mode="residual"`` clamps magnitudes only (no AR
    fill) — the gentle final pass can't inject energy.
    """
    x = np.asarray(x, dtype=np.float64)
    if not spans or len(x) < 64:
        return x.copy()
    spans = sorted((int(a), int(b)) for a, b in spans if b > a)
    if not spans:
        return x.copy()
    low, high = split_bands(x, sr)
    xf = max(1, int(sr * _XFADE_MS / 1000))

    flagged = np.zeros(len(x), bool)
    for a, b in spans:
        flagged[max(0, a - xf):min(len(x), b + xf)] = True

    # Repair-mode classification (plan Task 2).
    centres = [(a + b) / 2.0 for a, b in spans]
    gap = _BURST_GAP_S * sr
    tick_max = int(_TICK_MAX_MS * sr / 1000)
    mid = _band(x, sr, *_MID_BAND)

    out_high = high.copy()
    for i, (a, b) in enumerate(spans):
        a, b = max(0, a), min(len(x), b)
        if b <= a:
            continue
        burst = any(
            abs(centres[i] - centres[j]) <= gap
            for j in range(len(spans))
            if j != i
        )
        ma, mb = max(0, a - int(0.001 * sr)), min(len(x), b + int(0.001 * sr))
        in_speech = (
            float(np.sqrt((mid[ma:mb] ** 2).mean() + 1e-18))
            > 10.0 ** (_INSPEECH_DBFS / 20.0)
        )
        is_tick = (b - a) <= tick_max
        use_ar = (
            mode != "residual"
            and is_tick
            and not burst
            and not in_speech
        )
        ra = max(0, a - (int(_FILL_PRE_MS * sr / 1000) + xf))
        rb = min(len(x), b + int(_FILL_POST_MS * sr / 1000) + xf)

        patched_seg: np.ndarray | None = None
        p0, p1 = ra, rb
        if use_ar:
            rep = _ar_fill(high, ra, rb, flagged, sr)
            if rep is not None:
                n = rb - ra
                w = np.ones(n)
                k = min(xf, n // 2)
                if k > 0:
                    ramp = 0.5 - 0.5 * np.cos(np.pi * np.arange(k) / k)
                    w[:k] = ramp
                    w[n - k:] = ramp[::-1]
                patched_seg = high[ra:rb] * (1.0 - w) + rep * w
        if patched_seg is None:
            # STFT magnitude clamp path (bursts / wide / in-speech /
            # residual / AR fallback).
            p0 = max(0, a - int(_CLAMP_WRITE_MS * sr / 1000) - xf)
            p1 = min(len(x), b + int(_CLAMP_WRITE_MS * sr / 1000) + xf)
            patched_seg = _stft_clamp(high, p0, p1, sr)

        if patched_seg is not None:
            seg = patched_seg[: p1 - p0]
            if len(seg) == p1 - p0:
                out_high[p0:p1] = seg
            else:
                out_high[p0:p0 + len(seg)] = seg

        # Hard guard: never let a repaired span get louder in the high
        # band — revert to the original samples when it would.
        ga, gb = max(0, a - xf), min(len(x), b + xf)
        e_before = float((high[ga:gb] ** 2).sum())
        e_after = float((out_high[ga:gb] ** 2).sum())
        if e_after > e_before * (1.0 + 1e-6):
            out_high[p0:p1] = high[p0:p1]

    return low + out_high


__all__ = [
    "detect_mouth_clicks",
    "repair_mouth_clicks",
    "split_bands",
    "detect_events",
    "ClickEvent",
]
