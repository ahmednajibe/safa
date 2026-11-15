"""Mouth-click detection + band-limited repair (M4c Task 1).

Saliva clicks and lip smacks live almost entirely above ~2.5 kHz and last a
fraction of a millisecond to a few milliseconds — which is why ffmpeg's
full-band ``adeclick`` missed them (the low/mid voice energy masks the
prediction error) and why the fix works band-limited.

Detector (Argon §1 + Astra §1, tuned to the plan's constants):

- high band: zero-phase 4th-order Butterworth 2.5–12 kHz (``sosfiltfilt``);
  mid band 500–2500 Hz for the consonant tests.
- feature: 1.5 ms RMS envelope of the high band on 1 ms hops; local floor =
  40 ms sliding median + MAD. Candidates: level excess ≥ 2·z dB over that
  floor, or a sharp high-band edge (≥8 dB/ms) — the second path catches
  clicks inside speech where the floor is already elevated. Runs are only
  kept if some frame still reaches z-worth of excess.
- click width is measured within 6 dB of the run peak (0.4–6 ms); the raw
  above-floor run overstates it in silence because the decay tail keeps
  "elevated" over a −180 dB floor.
- band specificity: mid-band excess must stay small where the mid band is
  actually active, and no sharp mid-band edge may coincide with the
  click's sharpest high-band edge (a speech onset moves all bands).
- veto (consonant protection, gated to ≥ −60 dBFS so silence can't veto):
  the high-band envelope stays within 15 dB of the peak for >14 ms
  (sustained burst = consonant), or the mid-band level inside the span
  rises >8 dB over its ±15 ms neighbourhood.

Repair: split into low (<2.5 kHz) and high (everything above) with a
complementary filter so ``low + high`` rebuilds the input exactly; inside the
high band only, each ≤6 ms flagged span is replaced (+0.5 ms raised-cosine
crossfades) by bidirectional least-squares AR prediction (order 24, 10 ms of
unflagged context per side); spans <1 ms fall back to PCHIP. The <2.5 kHz
band and everything outside the flagged spans is bit-identical.
"""
from __future__ import annotations

import numpy as np
from scipy import interpolate, ndimage, signal

_HI_BAND = (2500.0, 12000.0)   # click band, Hz
_MID_BAND = (500.0, 2500.0)    # consonant co-energy band, Hz

_WIN_MS = 1.5        # RMS envelope window
_HOP_MS = 1.0        # envelope hop
_MED_FRAMES = 41     # ~40 ms sliding-median window (odd, envelope frames)

_MAD_SIGMA = 1.4826  # MAD → σ consistency factor
_DB_PER_Z = 2.0      # "≥12 dB over floor (z ≥ 6 MAD)" → 2 dB per z unit
_EDGE_DB = 8.0       # per-frame high-band edge that counts as a sharp onset

_MIN_W_FRAMES = 1    # click width = frames within 6 dB of the run peak …
_MAX_W_FRAMES = 6    # … 0.4–6 ms per the plan; the raw run may be longer
_MAX_RUN_FRAMES = 12 #   because near-silence floors keep the tail "elevated"
_MID_MAX_DB = 2.0    # band specificity: mid-band excess allowed regardless …
_MID_RATIO = 0.25    # … or under this share of the high-band excess
_MID_EDGE_DB = 5.0   # coincident mid-band edge floor …
_MID_EDGE_RATIO = 0.75  # … and as a share of the high-band edge → speech onset

_HOLD_DROP_DB = 15.0 # veto 1: envelope stays within this of the run peak …
_HOLD_MS = 14.0      # … longer than this → sustained burst = consonant
_MID_RISE_DB = 8.0   # veto 2: in-span mid level rises this far …
_MID_WIN_MS = 15.0   # … above the ±15 ms neighbourhood
_VETO_ABS_DB = -60.0 # vetoes only count above this absolute level — below it
                     # there is no consonant to protect (silence fluctuates
                     # many dB over a −180 dB median and would veto clicks)

_SPLIT_HZ = 2500.0   # repair band split
_AR_ORDER = 24
_AR_CTX_MS = 10.0    # clean context per side for the AR fit
_AR_MIN_CTX_MS = 2.0
_XFADE_MS = 0.5      # raised-cosine crossfade at each patch edge
_PCHIP_MAX_MS = 1.0  # spans shorter than this use PCHIP
_CLIP_FACTOR = 3.0   # hard cap on predicted amplitudes (× context peak)


def _band(x: np.ndarray, sr: int, lo: float, hi: float) -> np.ndarray:
    hi = min(hi, sr * 0.45)
    sos = signal.butter(4, [lo, hi], btype="band", fs=sr, output="sos")
    return signal.sosfiltfilt(sos, x)


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


def _envelope_db(
    x: np.ndarray, sr: int
) -> tuple[np.ndarray, int, int]:
    """1.5 ms RMS envelope in dB on 1 ms hops; frame ``i`` covers
    ``[i*hop, i*hop + win)`` samples."""
    win = max(1, int(round(sr * _WIN_MS / 1000)))
    hop = max(1, int(round(sr * _HOP_MS / 1000)))
    n = 1 + (len(x) - win) // hop
    if n <= 0:
        return np.zeros(0), win, hop
    fr = np.lib.stride_tricks.sliding_window_view(x, win)[::hop]
    rms = np.sqrt((fr * fr).mean(axis=1) + 1e-18)
    return 20 * np.log10(np.maximum(rms, 1e-9)), win, hop


def detect_mouth_clicks(
    x: np.ndarray, sr: int, z_thresh: float = 6.0
) -> list[tuple[int, int]]:
    """Detect mouth clicks in mono ``x``; returns sample spans.

    ``z_thresh`` is the sensitivity unit (plan: gentle=7, normal=6,
    strong=5); each unit is 2 dB of required excess over the local
    median+MAD floor.
    """
    x = np.asarray(x, dtype=np.float64)
    if len(x) < int(sr * 0.05):
        return []
    hi = _band(x, sr, *_HI_BAND)
    mid = _band(x, sr, *_MID_BAND)
    e_hi, win, hop = _envelope_db(hi, sr)
    e_mid, _, _ = _envelope_db(mid, sr)
    if len(e_hi) < _MED_FRAMES:
        return []

    med_hi = ndimage.median_filter(e_hi, _MED_FRAMES, mode="nearest")
    mad_hi = ndimage.median_filter(
        np.abs(e_hi - med_hi), _MED_FRAMES, mode="nearest"
    )
    med_mid = ndimage.median_filter(e_mid, _MED_FRAMES, mode="nearest")
    mad_mid = ndimage.median_filter(
        np.abs(e_mid - med_mid), _MED_FRAMES, mode="nearest"
    )
    floor_db = med_hi + _MAD_SIGMA * mad_hi
    floor_mid = med_mid + _MAD_SIGMA * mad_mid
    excess_hi = e_hi - floor_db
    excess_mid = e_mid - floor_mid

    # Two candidate paths, OR-ed per frame and with ≤2-frame gaps closed.
    # A: level — the click sticks ≥ 2·z dB out of the floor (plan's
    #    "≥12 dB, z≥6").
    # B: transient — a sharp high-band edge (≥8 dB/ms), catching clicks
    #    inside speech where the level contrast is lower but the onset is
    #    still instantaneous; a run is only kept if some frame in it still
    #    reaches z-worth of excess.
    edge_hi = np.abs(np.diff(e_hi, append=e_hi[-1]))
    edge_mid = np.abs(np.diff(e_mid, append=e_mid[-1]))
    cand = ndimage.binary_closing(
        (excess_hi >= z_thresh * _DB_PER_Z) | (edge_hi >= _EDGE_DB),
        structure=np.ones(3, bool),
    )

    d = np.diff(cand.astype(np.int8), prepend=0, append=0)
    starts, ends = np.where(d == 1)[0], np.where(d == -1)[0]
    hop_ms = hop / sr * 1000.0
    mid_pad = int(round(_MID_WIN_MS / hop_ms))

    spans: list[tuple[int, int]] = []
    for a, b in zip(starts, ends):
        eh = float(excess_hi[a:b].max())
        if eh < z_thresh:
            continue
        pk = float(e_hi[a:b].max())

        # Click width = frames within 6 dB of the peak (FWHM). The raw run
        # can be far longer in silence (the whole decay tail sits above a
        # −180 dB floor), so the run itself is only bounded loosely.
        w_eff = int((e_hi[a:b] > pk - 6.0).sum())
        if (
            not (_MIN_W_FRAMES <= w_eff <= _MAX_W_FRAMES)
            or (b - a) > _MAX_RUN_FRAMES
        ):
            continue

        # The click's core = near-peak frames; run edge frames belong to the
        # surrounding speech and must not feed the mid-band tests.
        core = np.where(e_hi[a:b] > pk - _HOLD_DROP_DB)[0]
        c0, c1 = a + int(core[0]), a + int(core[-1]) + 1

        # Band specificity: a click adds energy in the high band only, so
        # mid-band excess stays ≈0 where the mid band actually carries
        # energy (>-60 dBFS; below that its envelope is meaningless).
        lo, hi_ = max(0, c0 - 2), min(len(e_hi), c1 + 2)
        active = e_mid[lo:hi_] > _VETO_ABS_DB
        em = float(excess_mid[lo:hi_][active].max(initial=0.0))
        if em > max(_MID_MAX_DB, _MID_RATIO * eh):
            continue

        # A real speech onset moves all bands together: a sharp mid-band
        # edge coincident with the click's sharpest high-band edge →
        # consonant, not a click.
        fa = a + int(np.argmax(edge_hi[a:b]))
        edg_m = float(edge_mid[max(0, fa - 1): fa + 2].max())
        if (
            e_mid[fa] > _VETO_ABS_DB
            and edg_m > max(_MID_EDGE_DB, _MID_EDGE_RATIO * edge_hi[fa])
        ):
            continue

        # Veto 1: the high band stays within 15 dB of the peak for >14 ms
        # → sustained consonant burst. A click decays fast even in silence;
        # measuring from the peak (not the −180 dB floor) keeps near-zero
        # neighbourhoods from vetoing clicks.
        near = e_hi > max(pk - _HOLD_DROP_DB, _VETO_ABS_DB)
        h0, h1 = a, b - 1
        while h0 > 0 and near[h0 - 1]:
            h0 -= 1
        while h1 + 1 < len(near) and near[h1 + 1]:
            h1 += 1
        if (h1 - h0 + 1) * hop_ms > _HOLD_MS:
            continue

        # Veto 2: the mid band inside the click core rises >8 dB over its
        # ±15 ms neighbourhood → speech onset (only where mid carries
        # energy).
        cl_, cr_ = max(0, c0 - mid_pad), min(len(e_mid), c1 + mid_pad)
        ctx = np.concatenate([e_mid[cl_:c0], e_mid[c1:cr_]])
        ctx = ctx[ctx > _VETO_ABS_DB]
        ctx_med = float(np.median(ctx)) if len(ctx) else -np.inf
        if e_mid[c0:c1].max() > _VETO_ABS_DB and (
            float(e_mid[c0:c1].max()) - ctx_med > _MID_RISE_DB
        ):
            continue

        # Repair span: the near-peak core plus the RMS-window tail.
        start = c0 * hop
        end = min(len(x), (c1 - 1) * hop + win)
        spans.append((int(start), int(end)))
    return spans


# ---- repair ----------------------------------------------------------------


def _ar_predict(ctx: np.ndarray, order: int, n_out: int) -> np.ndarray | None:
    """Least-squares AR fit ``x[t] ≈ Σ a_k x[t-k-1]`` on ``ctx``, iterated
    ``n_out`` samples forward. Returns None when the fit can't be trusted."""
    if len(ctx) < order * 2 or len(ctx) - order <= order:
        return None
    win = np.lib.stride_tricks.sliding_window_view(ctx, order)
    X = win[: len(ctx) - order][:, ::-1]
    try:
        coef, *_ = np.linalg.lstsq(X, ctx[order:], rcond=None)
    except np.linalg.LinAlgError:
        return None
    clip = _CLIP_FACTOR * float(np.abs(ctx).max()) + 1e-6
    buf = np.empty(order + n_out)
    buf[:order] = ctx[-order:]
    for i in range(n_out):
        v = float(coef @ buf[i:i + order][::-1])
        buf[order + i] = np.clip(v, -clip, clip)
    out = buf[order:]
    return out if np.isfinite(out).all() else None


def _pchip_fill(
    high: np.ndarray, ra: int, rb: int, flagged: np.ndarray, sr: int
) -> np.ndarray:
    """PCHIP-interpolate the flagged region from unflagged knots at its
    sides — the <1 ms span path (plan: 'fall back to PCHIP')."""
    n = rb - ra
    side = int(0.003 * sr)
    li = np.arange(max(0, ra - side), ra)
    ri = np.arange(rb, min(len(high), rb + side))
    lx = li[~flagged[li]][-8:]
    rx = ri[~flagged[ri]][:8]
    kx = np.r_[lx, rx]
    if len(kx) < 2:
        return np.zeros(n)
    ky = high[kx]
    xs = np.arange(ra, rb, dtype=np.float64)
    try:
        rep = interpolate.PchipInterpolator(kx, ky, extrapolate=True)(xs)
    except ValueError:
        rep = np.interp(xs, kx, ky)
    limit = max(1e-6, 1.5 * float(np.abs(ky).max()))
    return np.clip(np.asarray(rep, dtype=np.float64), -limit, limit)


def _ar_fill(
    high: np.ndarray, ra: int, rb: int, flagged: np.ndarray, sr: int
) -> np.ndarray | None:
    """Bidirectional AR fill: predict the region from each side's clean
    context and blend with a raised-cosine weight (forward fades out,
    backward fades in)."""
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


def repair_mouth_clicks(
    x: np.ndarray, sr: int, spans: list[tuple[int, int]]
) -> np.ndarray:
    """Repair ``spans`` in mono ``x``; same length out, float64.

    Only the >2.5 kHz band inside the flagged spans (+0.5 ms crossfades) is
    modified; everything else passes through bit-identically.
    """
    x = np.asarray(x, dtype=np.float64)
    if not spans or len(x) < 64:
        return x.copy()
    low, high = split_bands(x, sr)
    xf = max(1, int(sr * _XFADE_MS / 1000))

    flagged = np.zeros(len(x), bool)
    for a, b in spans:
        flagged[max(0, a - xf):min(len(x), b + xf)] = True

    pchip_max = int(_PCHIP_MAX_MS * sr / 1000)
    out_high = high.copy()
    for a, b in spans:
        ra, rb = max(0, a - xf), min(len(x), b + xf)
        n = rb - ra
        if n <= 0:
            continue
        rep = (
            _pchip_fill(high, ra, rb, flagged, sr)
            if n <= pchip_max
            else _ar_fill(high, ra, rb, flagged, sr)
        )
        if rep is None:
            rep = _pchip_fill(high, ra, rb, flagged, sr)
        # raised-cosine edge crossfades between original and repair
        w = np.ones(n)
        k = min(xf, n // 2)
        if k > 0:
            ramp = 0.5 - 0.5 * np.cos(np.pi * np.arange(k) / k)
            w[:k] = ramp
            w[n - k:] = ramp[::-1]
        out_high[ra:rb] = high[ra:rb] * (1.0 - w) + rep * w
    return low + out_high


__all__ = ["detect_mouth_clicks", "repair_mouth_clicks", "split_bands"]
