"""enhance — voice polish (SPEC §2): pedalboard studio chain streamed in
10 s blocks (``reset=False`` keeps filter/gate/comp state across blocks),
then an optional ffmpeg ``deesser`` pass.

M4c rework — all thresholds are computed per file, so a quieter
recording gets the same *relative* processing. M4d: the calibration
comes from ``ctx.analysis`` when an earlier VAD stage measured it
(pre-cut), otherwise from VAD-gated stats on this input — floor over
non-speech frames, speech over speech frames, never all-frame
percentiles (once pauses are cut, quiet speech would pollute both).
The M4d chain order puts the compressor *before* the presence boost,
so EQ'd transient energy gets compressed and the residual declick
pass only sees what survives:

1. HighpassFilter 95 Hz ×2 (24 dB/oct) — the "bass under the voice" fix.
   It runs before the gate so LF rumble peaks can't hold the gate open
   (measured −9 dB extra floor reduction on the noisiest recording).
2. De-boom EQ (``reduce_boom``, default on): PeakFilter 135 Hz −3.5 dB
   Q 0.7 and 260 Hz −2.5 dB Q 1.2.
3. NoiseGate expander — still before anything that *adds* gain (only
   attenuating filters precede it): threshold
   ``min(floor + 10, speech − 18)``, ratio 2.5, 2 ms/150 ms — pushes the
   quiet-part floor down instead of raising it.

   M4d fix round: the expander must never attenuate INSIDE VAD speech.
   The level detector cannot tell a −60 dB syllable tail from pause
   noise — the VAD can — so ``ctx.analysis['speech_intervals']``
   (published by the first VAD stage, re-expressed in output time by
   ``silence`` when it cuts) gates the gate: the chain is split at the
   NoiseGate into pre/post and run twice (with and without the gate),
   and the two zero-latency signals are mixed by a per-sample mask that
   is 1 inside speech ±60 ms hangover. After a ``remove`` cut the only
   expander targets left are the short kept pauses.
4. Compressor threshold ``speech − 2`` dB, ratio 2.5, 15 ms/100 ms, no
   make-up gain (the loudness op sets level).
5. Presence: PeakFilter 3.5 kHz +1.8 dB Q 1.0, HighShelfFilter 10 kHz
   +1.0 dB.
6. Optional ffmpeg ``deesser``.

Presets (registry ``preset`` param) derive from voice as before:
- ``bright``: stronger presence (+3 dB) and shelf (+2 dB).
- ``warm``: no boom cut, +1.5 dB @150 Hz, mild presence, no shelf.
"""
from __future__ import annotations

import dataclasses
import os
from pathlib import Path

from pedalboard import (
    Compressor,
    HighShelfFilter,
    HighpassFilter,
    NoiseGate,
    PeakFilter,
    Pedalboard,
)
from pedalboard.io import AudioFile

import numpy as np

from backend.media.ffmpeg_run import run_filter
from backend.media.loudness import level_stats
from backend.operations.base import OpContext, OpResult, stage_wav
from backend.pipeline.types import CancelledError

_BLOCK_S = 10.0
_DEES_SHARE = 0.8  # pedalboard pass covers 0..80 % when the deesser follows
_DEESER = "deesser=i=0.4:m=0.5:f=0.5"
_HANG_S = 0.060    # expander stays off ~60 ms around speech edges
_MASK_RAMP_S = 0.010


def _plugins(
    preset: str, floor_dbfs: float, speech_dbfs: float, reduce_boom: bool
) -> list:
    gate_db = min(floor_dbfs + 10.0, speech_dbfs - 18.0)
    comp_db = speech_dbfs - 2.0
    gate = NoiseGate(
        threshold_db=gate_db, ratio=2.5, attack_ms=2, release_ms=150
    )
    comp = Compressor(
        threshold_db=comp_db, ratio=2.5, attack_ms=15, release_ms=100
    )
    chain = [
        HighpassFilter(cutoff_frequency_hz=95),
        HighpassFilter(cutoff_frequency_hz=95),
    ]
    if preset != "warm" and reduce_boom:
        chain += [
            PeakFilter(cutoff_frequency_hz=135, gain_db=-3.5, q=0.7),
            PeakFilter(cutoff_frequency_hz=260, gain_db=-2.5, q=1.2),
        ]
    if preset == "warm":
        chain += [
            gate,
            PeakFilter(cutoff_frequency_hz=150, gain_db=1.5, q=1.0),
            comp,
            PeakFilter(cutoff_frequency_hz=3500, gain_db=1.2, q=1.0),
        ]
    elif preset == "bright":
        chain += [
            gate,
            comp,
            PeakFilter(cutoff_frequency_hz=3500, gain_db=3.0, q=1.0),
            HighShelfFilter(cutoff_frequency_hz=10000, gain_db=2.0),
        ]
    else:
        chain += [
            gate,
            comp,
            PeakFilter(cutoff_frequency_hz=3500, gain_db=1.8, q=1.0),
            HighShelfFilter(cutoff_frequency_hz=10000, gain_db=1.0),
        ]
    return chain


def _calibration(
    in_wav: Path, ctx: OpContext
) -> tuple[float, float, list[tuple[float, float]] | None]:
    """(floor_dbfs, speech_dbfs, speech_intervals|None) (M4d).

    Carried analysis first — an earlier VAD stage measured it on the
    pre-cut audio. Otherwise compute VAD-gated stats on this input
    (floor on non-speech, speech on speech); never all-frame
    percentiles, which misread quiet speech as floor after a cut.

    ``speech_intervals`` drive the expander bypass mask ONLY when they
    were carried through ``ctx.analysis`` by an upstream stage
    (``silence`` re-expresses them in output time after the cut) and
    their duration matches this input — that is exactly the situation
    the mask exists for: after ``action=remove`` the only non-speech
    left is the short kept pauses, so the gate may work there but must
    never touch speech. When no stage ran VAD earlier (standalone
    enhance), intervals recomputed here would describe the same signal,
    but enabling the mask would also change the calibrated pre-M4d
    floor-suppression targets — so ``None`` is returned and the
    expander works everywhere as before.
    """
    floor = ctx.analysis.get("noise_floor_dbfs")
    speech = ctx.analysis.get("speech_dbfs")
    iv = ctx.analysis.get("speech_intervals")
    iv_dur = ctx.analysis.get("speech_intervals_dur")
    carried_iv = None
    if (
        iv is not None and iv_dur is not None
        and abs(float(iv_dur) - ctx.duration_s) <= 0.02
    ):
        carried_iv = [
            (float(a), float(b)) for a, b in iv if float(b) > float(a)
        ] or None
    if floor is not None and speech is not None:
        return float(floor), float(speech), carried_iv
    try:
        from backend.media.vad_stats import vad_level_stats

        stats = vad_level_stats(
            in_wav, should_cancel=ctx.should_cancel
        )
        return (
            stats["noise_floor_dbfs"], stats["speech_dbfs"], carried_iv
        )
    except Exception:
        # VAD can't run (module missing / degenerate file) — keep the
        # pre-M4d all-frame behaviour rather than fail the job.
        stats = level_stats(in_wav)
        return stats["floor_dbfs"], stats["speech_dbfs"], None


def _gate_mask(
    intervals: list[tuple[float, float]], off: int, n: int, sr: int
) -> np.ndarray:
    """(1, n) float weights selecting the *ungated* pre-chain: 1 inside
    speech ± ``_HANG_S`` hangover, 10 ms linear ramps outside — the two
    pre-chains are zero-latency, so a linear crossfade is transparent."""
    m = np.zeros(n, dtype=np.float32)
    ramp = max(1, int(_MASK_RAMP_S * sr))
    hang = int(_HANG_S * sr)
    for a_s, b_s in intervals:
        lo = int(round(a_s * sr)) - hang - ramp - off
        hi = int(round(b_s * sr)) + hang + ramp - off
        if hi <= 0 or lo >= n:
            continue
        i0, i1 = max(0, lo), min(n, hi)
        idx = np.arange(i0, i1, dtype=np.float64)
        w = np.clip(np.minimum(idx - lo, hi - idx) / ramp, 0.0, 1.0)
        np.maximum(m[i0:i1], w.astype(np.float32), out=m[i0:i1])
    return m[None, :]


def run(in_wav: Path, params: dict, ctx: OpContext) -> OpResult:
    params = params or {}
    deess = bool(params.get("deess", True))
    reduce_boom = bool(params.get("reduce_boom", True))
    floor_dbfs, speech_dbfs, mask_iv = _calibration(in_wav, ctx)
    out = stage_wav(ctx, "enhance")
    tmp = ctx.work_dir / "enhance_pedalboard.wav"
    share = _DEES_SHARE if deess else 1.0
    plugins = _plugins(
        params.get("preset", "voice"),
        floor_dbfs, speech_dbfs, reduce_boom,
    )
    gi = next(
        (i for i, p in enumerate(plugins) if isinstance(p, NoiseGate)),
        None,
    )
    if mask_iv and gi is not None:
        # Speech-masked expander (module docstring §3): gated/open twin
        # pre-chains, linear mask mix, one shared post chain so the
        # compressor reacts to the actually-rendered signal.
        pre_gated = Pedalboard(plugins[: gi + 1])
        pre_open = Pedalboard(plugins[:gi])
        post = Pedalboard(plugins[gi + 1:])

        def _process(data: np.ndarray, off: int, sr: int) -> np.ndarray:
            gated = pre_gated.process(data, sr, reset=False)
            opened = pre_open.process(data, sr, reset=False)
            m = _gate_mask(mask_iv, off, data.shape[-1], sr)
            return post.process(
                opened * m + gated * (1.0 - m), sr, reset=False
            )
    else:
        board = Pedalboard(plugins)

        def _process(data: np.ndarray, off: int, sr: int) -> np.ndarray:
            return board.process(data, sr, reset=False)

    try:
        with AudioFile(str(in_wav)) as f:
            sr = int(f.samplerate)
            total = f.frames
            block = int(sr * _BLOCK_S)
            done = 0
            with AudioFile(str(tmp), "w", sr, f.num_channels) as o:
                while done < total:
                    if ctx.should_cancel():
                        raise CancelledError()
                    data = f.read(min(block, total - done))
                    off = done
                    done += data.shape[-1]
                    # no latency plugins → same frame count in and out
                    o.write(_process(data, off, sr))
                    ctx.report(share * done / total, None)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise

    if not deess:
        os.replace(tmp, out)
        ctx.report(1.0, None)
        return OpResult(path=out)

    sub = dataclasses.replace(
        ctx,
        report=lambda p, m: ctx.report(
            _DEES_SHARE + (1.0 - _DEES_SHARE) * (p or 0.0), m
        ),
    )
    try:
        run_filter(tmp, out, _DEESER, sub)
    finally:
        tmp.unlink(missing_ok=True)
    return OpResult(path=out)
