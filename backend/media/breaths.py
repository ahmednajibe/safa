"""Frame-level breath detection (M6b Task 1, design docs/brainstorm/argon_breath.md §2).

Silero VAD folds most breaths into speech segments, so breaths must be
found *inside* speech, not only in pauses. Features are computed on
20 ms frames at a 10 ms hop on the mono mix in two stages:

- **Seed** (strict, per Argon §2): ``p_speech`` < 0.35 (< 0.55 within
  150 ms before a VAD onset), level in ``[floor+6, speech-8]`` dBFS,
  unvoiced ``r_max`` < 0.35, ``E(0.4–4.5k)/E_total`` > 0.65,
  ``E(5.5–16k)/E(0.4–4.5k)`` < 0.8, flatness (0.4–6 kHz) > 0.22,
  centroid 800–3200 Hz. A run ≥ 80 ms seeds a candidate.
- **Grow**: the seed's span extends outward while a looser
  "breath-plausible" mask holds — real breath onsets are HF-tilted
  (centroid up to ~5 kHz) and their decay drops flatness below the
  strict gate, so measuring duration/onset on the clipped strict run
  rejects true breaths (observed on the real fixtures).

Deviations from the letter of Argon §2, validated on the fixtures:

- ``floor_dbfs`` is the P10 of non-speech frames (``p_speech`` < 0.3),
  i.e. the real room tone. The ``level_stats`` P20 over all frames
  lands on the speech/quiet boundary on pause-heavy files (~-56 dBFS
  when the true bed is ~-85) and rejects every real breath.
- Grow-level p gate is 0.65: Silero assigns 0.4–0.6 mid-breath on these
  recordings (the "folded" frames §2 warns about), so a 0.35 cap clips
  the breath core itself.
- Onset time-to-peak ≥ 20 ms instead of 40 ms: measured on the grown
  span, real inhales can attack in 20–30 ms (a fricative hit then the
  breath body); the strict 40 ms rejects them. Clicks still fail the
  ≥ 80 ms strict-seed requirement plus duration and spectral gates.

Post-grow gates stay as designed: contiguous span 160–900 ms
(micro-gaps ≤ 30 ms merged), and the 45 ms pre-voicing collar before
each word's first voiced frame (``r_max`` ≥ 0.45) protects /h/ and
soft word starts.

``detect_breaths`` returns ``[(start_s, end_s)]``; ``silero_frame_probs``
produces the 10 ms-grid probability array it consumes (model's native
32 ms window, linearly interpolated).
"""
from __future__ import annotations

import numpy as np

FRAME_S = 0.020
HOP_S = 0.010

_VAD_SR = 16000
_VAD_CHUNK = 512  # 32 ms at 16 kHz — the model's native window

# Sensitivity presets (op param): scale the selectivity gates.
SENSITIVITIES = {
    "gentle": dict(p_low=0.30, p_high=0.50, min_dur=0.20, flat=0.26),
    "normal": dict(p_low=0.35, p_high=0.55, min_dur=0.16, flat=0.22),
    "strong": dict(p_low=0.45, p_high=0.65, min_dur=0.12, flat=0.18),
}
_SEED_MIN_S = 0.080

_MODEL = None


def _vad_model():
    global _MODEL
    if _MODEL is None:
        from silero_vad import load_silero_vad

        _MODEL = load_silero_vad(onnx=True)  # ~2.5x faster than jit
    return _MODEL


def _mono(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=np.float32)
    if x.ndim == 2:
        return np.ascontiguousarray(x.mean(axis=1), dtype=np.float32)
    return np.ascontiguousarray(x.ravel(), dtype=np.float32)


def _n_frames(n_samples: int, sr: int) -> int:
    frame, hop = int(sr * FRAME_S), int(sr * HOP_S)
    if n_samples < frame:
        return 0
    return 1 + (n_samples - frame) // hop


def silero_frame_probs(x: np.ndarray, sr: int) -> np.ndarray:
    """Speech probability per 10 ms frame on ``x``'s time grid.

    Runs the bundled Silero VAD on a 16 kHz mono copy in its native
    non-overlapping 512-sample (32 ms) chunks, then linearly interpolates
    the per-chunk probabilities onto the 20 ms/10 ms frame grid used by
    ``detect_breaths`` (same frame count for the same signal).
    """
    import soxr
    import torch

    mono = _mono(x)
    n_fr = _n_frames(len(mono), sr)
    if n_fr == 0:
        return np.zeros(0, dtype=np.float32)
    if sr != _VAD_SR:
        x16 = soxr.resample(mono, sr, _VAD_SR, quality="VHQ")
    else:
        x16 = mono.astype(np.float32)

    model = _vad_model()
    model.reset_states()
    n_chunks = max(1, int(np.ceil(len(x16) / _VAD_CHUNK)))
    padded = np.zeros(n_chunks * _VAD_CHUNK, dtype=np.float32)
    padded[: len(x16)] = x16
    probs = np.empty(n_chunks, dtype=np.float32)
    with torch.no_grad():
        for k in range(n_chunks):
            chunk = torch.from_numpy(
                padded[k * _VAD_CHUNK: (k + 1) * _VAD_CHUNK]
            )
            probs[k] = float(model(chunk, _VAD_SR).item())
    # chunk k covers [k*32ms, k*32ms+32ms] → prob at its centre
    t_chunk = (np.arange(n_chunks) + 0.5) * (_VAD_CHUNK / _VAD_SR)
    t_frame = (
        np.arange(n_fr) * int(sr * HOP_S) + int(sr * FRAME_S) / 2
    ) / sr
    return np.interp(t_frame, t_chunk, probs).astype(np.float32)


def _frame_features(mono: np.ndarray, sr: int) -> dict[str, np.ndarray]:
    """Per-frame scalar features; block FFT bounds RAM on long files."""
    frame, hop = int(sr * FRAME_S), int(sr * HOP_S)
    n_fr = 1 + (len(mono) - frame) // hop
    n_fft = 2048
    freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
    band = (freqs >= 400.0) & (freqs <= 4500.0)
    hf = (freqs >= 5500.0) & (freqs <= 16000.0)
    flat_band = (freqs >= 400.0) & (freqs <= 6000.0)
    lag_lo, lag_hi = int(sr / 400.0), int(sr / 70.0)
    win = np.hanning(frame).astype(np.float32)

    out = {
        k: np.empty(n_fr, dtype=np.float32)
        for k in ("rms_db", "r_max", "band", "hf", "flat", "centroid")
    }
    block = 6000  # 60 s of frames per FFT block (~12 MB spectrum)
    for i0 in range(0, n_fr, block):
        i1 = min(n_fr, i0 + block)
        idx = hop * np.arange(i0, i1)[:, None] + np.arange(frame)[None, :]
        fr = mono[idx]
        rms = np.sqrt((fr * fr).mean(axis=1) + 1e-18)
        out["rms_db"][i0:i1] = 20.0 * np.log10(np.maximum(rms, 1e-9))

        spec = np.abs(np.fft.rfft(fr * win[None, :], n=n_fft, axis=1)) ** 2
        tot = spec.sum(axis=1) + 1e-20
        e_band = spec[:, band].sum(axis=1)
        out["band"][i0:i1] = e_band / tot
        out["hf"][i0:i1] = spec[:, hf].sum(axis=1) / (e_band + 1e-20)
        pf = spec[:, flat_band] + 1e-20
        out["flat"][i0:i1] = (
            np.exp(np.log(pf).mean(axis=1)) / pf.mean(axis=1)
        )
        out["centroid"][i0:i1] = (
            (freqs[None, :] * spec).sum(axis=1) / tot
        )
        # normalized autocorrelation: zero-padded rfft → linear ac to 685
        ac = np.fft.irfft(spec, n=n_fft, axis=1)[:, : lag_hi + 1]
        ac0 = ac[:, 0]
        r = np.where(
            ac0[:, None] > 1e-12, ac / np.maximum(ac0[:, None], 1e-12), 0.0
        )
        out["r_max"][i0:i1] = r[:, lag_lo: lag_hi + 1].max(axis=1)
    return out


def _level_stats(
    feats: dict[str, np.ndarray], p: np.ndarray
) -> tuple[float, float]:
    """(floor_dbfs, speech_dbfs) for the level gate.

    Floor = P10 of the VAD non-speech frames (``p < 0.3``) — the room
    tone a breath must clear. P10 over *all* frames is the fallback
    when VAD finds too little silence (short/speechy inputs).
    ``speech_dbfs`` = P85 of all frames (``loudness.level_stats`` twin).
    """
    rms = feats["rms_db"]
    ns = rms[p < 0.3]
    base = ns if len(ns) >= 30 else rms
    floor = float(np.percentile(base, 10))
    return floor, float(np.percentile(rms, 85))


def whole_file_level_stats(
    wav,
    speech: list[tuple[float, float]],
    should_cancel=None,
) -> tuple[float, float]:
    """(floor_dbfs, speech_dbfs) measured over the WHOLE file (R1 B9).

    ``detect_breaths`` derives these from whatever slice it is handed;
    beside a pause longer than ~11 s the P85 lands on silence and the
    seed level gate empties. The stats are therefore measured once here
    on streamed blocks — a frame counts as speech when its centre falls
    inside a VAD speech interval, everything else forms the non-speech
    pool the P10 floor comes from (mirrors ``_level_stats``).
    """
    import soundfile as sf

    info = sf.info(str(wav))
    sr, n = info.samplerate, info.frames
    if n == 0:
        return -120.0, -120.0
    frame, hop = int(sr * FRAME_S), int(sr * HOP_S)
    bounds = np.array(
        [(a * sr, b * sr) for a, b in speech], dtype=np.float64
    ).reshape(-1, 2)
    rms_all: list[np.ndarray] = []
    speech_flag: list[np.ndarray] = []
    tail = np.zeros(0, np.float32)
    tail_start = 0
    pos = 0
    with sf.SoundFile(str(wav), "r") as fin:
        while pos < n:
            if should_cancel is not None and should_cancel():
                from backend.pipeline.types import CancelledError

                raise CancelledError()
            blk = fin.read(
                min(1 << 16, n - pos), dtype="float32", always_2d=True
            )
            if not len(blk):
                break
            pos += len(blk)
            buf = np.concatenate([tail, blk.mean(axis=1)])
            base = tail_start
            if len(buf) < frame:
                tail = buf
                continue
            n_fr = (len(buf) - frame) // hop + 1
            idx = hop * np.arange(n_fr)[:, None] + np.arange(frame)
            fr = buf[idx]
            rms = 20.0 * np.log10(
                np.maximum(
                    np.sqrt((fr * fr).mean(axis=1) + 1e-18), 1e-9
                )
            ).astype(np.float32)
            centres = base + hop * np.arange(n_fr) + frame // 2
            if len(bounds):
                ins = (
                    (centres[:, None] >= bounds[None, :, 0])
                    & (centres[:, None] < bounds[None, :, 1])
                ).any(axis=1)
            else:
                ins = np.zeros(n_fr, dtype=bool)
            rms_all.append(rms)
            speech_flag.append(ins)
            tail = buf[hop * n_fr:]
            tail_start = base + hop * n_fr
    if not rms_all:
        return -120.0, -120.0
    rms = np.concatenate(rms_all)
    ns = rms[~np.concatenate(speech_flag)]
    base_arr = ns if len(ns) >= 30 else rms
    return (
        float(np.percentile(base_arr, 10)),
        float(np.percentile(rms, 85)),
    )


def _runs(mask: np.ndarray) -> list[list[int]]:
    """Inclusive [start, end] index runs of True in ``mask``."""
    d = np.diff(mask.astype(np.int8), prepend=0, append=0)
    starts, ends = np.where(d == 1)[0], np.where(d == -1)[0]
    return [[int(a), int(b - 1)] for a, b in zip(starts, ends)]


def _voiced_onsets(r_max: np.ndarray, n_fr: int) -> list[int]:
    """Frame index where each voiced run (r_max ≥ 0.45, ≥ 2 frames)
    starts — the word onsets the 45 ms collar is measured from."""
    voiced = r_max >= 0.45
    onsets = []
    for a, b in _runs(voiced):
        if b - a + 1 >= 2:
            onsets.append(a)
    return onsets


def detect_breaths(
    x: np.ndarray,
    sr: int,
    speech_prob: np.ndarray | None = None,
    *,
    sensitivity: str = "normal",
    return_detail: bool = False,
    level_stats: tuple[float, float] | None = None,
) -> list[tuple[float, float]] | tuple[list[tuple[float, float]], dict]:
    """Breath spans ``[(start_s, end_s)]`` in ``x`` (mono or stereo).

    ``speech_prob`` may carry a precomputed 10 ms-grid VAD probability
    array (``silero_frame_probs``); when omitted the model runs here.
    ``sensitivity`` scales the p_speech/duration/flatness gates
    (gentle = fewer, stronger candidates only; strong = looser).
    With ``return_detail=True`` the result is ``(spans, detail)`` where
    ``detail`` carries the level stats (``floor_dbfs``, ``speech_dbfs``),
    the 10 ms frame RMS array (``rms_db``) and the voiced-onset frame
    indices (``voiced_onsets``) the op needs for the floor clamp and the
    fast-release check.
    """
    cfg = SENSITIVITIES.get(sensitivity, SENSITIVITIES["normal"])
    mono = _mono(x)
    frame, hop = int(sr * FRAME_S), int(sr * HOP_S)
    n_fr = _n_frames(len(mono), sr)
    empty = ([], {}) if return_detail else []
    if n_fr < 16:  # can't reach the 160 ms minimum
        return empty

    if speech_prob is None:
        p = silero_frame_probs(mono, sr)
    else:
        p = np.asarray(speech_prob, dtype=np.float32).ravel()
    if len(p) != n_fr:
        t_old = np.linspace(0.0, 1.0, len(p)) if len(p) else np.zeros(1)
        p = np.interp(
            np.linspace(0.0, 1.0, n_fr), t_old, p if len(p) else [0.0]
        ).astype(np.float32)

    feats = _frame_features(mono, sr)
    if level_stats is None:
        floor_db, speech_db = _level_stats(feats, p)
    else:
        # R1 B9: caller-measured whole-file stats keep the level gate
        # honest when ``x`` is a mostly-silent window near a splice.
        floor_db, speech_db = level_stats
    if speech_db - 4.0 <= floor_db + 3.0:
        return empty  # level gate is empty (e.g. digital silence)

    # VAD onsets (p ≥ 0.5 rising edges) → the 150 ms pre-onset relaxation.
    speech = p >= 0.5
    onsets = np.where(speech & ~np.concatenate(([False], speech[:-1])))[0]
    pre_onset = np.zeros(n_fr, dtype=bool)
    for o in onsets:
        pre_onset[max(0, o - 15): o] = True
    p_thr = np.where(pre_onset, cfg["p_high"], cfg["p_low"])

    rms = feats["rms_db"]
    # Strict seed mask — Argon §2 thresholds verbatim.
    seed = (
        (p < p_thr)
        & (rms >= floor_db + 6.0)
        & (rms <= speech_db - 8.0)
        & (feats["r_max"] < 0.35)
        & (feats["band"] > 0.65)
        & (feats["hf"] < 0.8)
        & (feats["flat"] > cfg["flat"])
        & (feats["centroid"] >= 800.0)
        & (feats["centroid"] <= 3200.0)
    )
    # Loose growth mask — the strict gates clip breath edges (HF-tilted
    # attack, tonal-decay tail, VAD-folded core at p 0.4–0.6), which then
    # breaks the duration and time-to-peak measurements. Growth only
    # needs the frames to stay non-speech, unvoiced-ish and noise-like.
    grow = (
        (p < 0.65)
        & (rms >= floor_db + 3.0)
        & (rms <= speech_db - 4.0)
        & (feats["r_max"] < 0.40)
        & (feats["band"] > 0.40)
        & (feats["hf"] < 1.5)
        & (feats["flat"] > 0.06)
        & (feats["centroid"] >= 500.0)
        & (feats["centroid"] <= 5500.0)
    )
    # Bridge micro-gaps ≤ 30 ms inside both masks (gated dropouts are
    # still part of the same breath).
    def _filled(m: np.ndarray) -> np.ndarray:
        out = m.copy()
        for a, b in _runs(~m):
            if b - a + 1 <= 3 and a > 0 and b < len(m) - 1:
                out[a: b + 1] = True
        return out

    seed_f, grow_f = _filled(seed), _filled(grow)
    seed_runs = [
        (a, b) for a, b in _runs(seed_f)
        if (b - a + 1) * HOP_S >= _SEED_MIN_S
    ]

    voiced_onsets = _voiced_onsets(feats["r_max"], n_fr)
    spans: list[tuple[float, float]] = []
    for a, b in seed_runs:
        # Grow outward while breath-plausible frames continue.
        while a > 0 and grow_f[a - 1]:
            a -= 1
        while b < n_fr - 1 and grow_f[b + 1]:
            b += 1
        dur = (b - a + 1) * HOP_S + (FRAME_S - HOP_S)
        if not (cfg["min_dur"] <= dur <= 0.9):
            continue
        end_s = b * HOP_S + FRAME_S
        start_s = a * HOP_S
        # 45 ms pre-voicing collar: clip the span where it meets the
        # first voiced onset inside/just after it (pre-aspirated /h/,
        # soft word attacks stay untouched).
        nxt = next(
            (o for o in voiced_onsets if o * HOP_S >= start_s - 1e-9),
            None,
        )
        if nxt is not None and nxt * HOP_S - 0.045 < end_s:
            end_s = nxt * HOP_S - 0.045
        if end_s - start_s < cfg["min_dur"]:
            continue
        # onset time-to-peak ≥ 20 ms (breaths attack in ~20–30 ms;
        # knocks < 10 ms are already stopped by the 80 ms seed)
        peak = a + int(np.argmax(rms[a: b + 1]))
        if (peak - a) * HOP_S < 0.020:
            continue
        spans.append((float(start_s), float(end_s)))
    # Adjacent seeds can grow into each other — merge overlaps.
    spans.sort()
    merged: list[list[float]] = []
    for s, e in spans:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    result = [(s, e) for s, e in merged]
    if not return_detail:
        return result
    detail = {
        "floor_dbfs": floor_db,
        "speech_dbfs": speech_db,
        "rms_db": rms,
        "voiced_onsets": voiced_onsets,
        "hop_s": HOP_S,
        "frame_s": FRAME_S,
    }
    return result, detail
