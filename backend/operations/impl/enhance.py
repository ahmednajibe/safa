"""enhance — voice polish (SPEC §2): pedalboard studio chain streamed in
10 s blocks (``reset=False`` keeps filter/gate/comp state across blocks),
then an optional ffmpeg ``deesser`` pass.

M4c rework — all thresholds are computed per file from
``loudness.level_stats`` (20 ms frame-RMS percentiles), so a quieter
recording gets the same *relative* processing:

1. HighpassFilter 95 Hz ×2 (24 dB/oct) — the "bass under the voice" fix.
   It runs before the gate so LF rumble peaks can't hold the gate open
   (measured −9 dB extra floor reduction on the noisiest recording).
2. De-boom EQ (``reduce_boom``, default on): PeakFilter 135 Hz −3.5 dB
   Q 0.7 and 260 Hz −2.5 dB Q 1.2.
3. NoiseGate expander — still before anything that *adds* gain (only
   attenuating filters precede it): threshold
   ``min(floor + 10, speech − 18)``, ratio 2.5, 2 ms/150 ms — pushes the
   quiet-part floor down instead of raising it.
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

from backend.media.ffmpeg_run import run_filter
from backend.media.loudness import level_stats
from backend.operations.base import OpContext, OpResult, stage_wav
from backend.pipeline.types import CancelledError

_BLOCK_S = 10.0
_DEES_SHARE = 0.8  # pedalboard pass covers 0..80 % when the deesser follows
_DEESER = "deesser=i=0.4:m=0.5:f=0.5"


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
            PeakFilter(cutoff_frequency_hz=3500, gain_db=1.2, q=1.0),
            comp,
        ]
    elif preset == "bright":
        chain += [
            gate,
            PeakFilter(cutoff_frequency_hz=3500, gain_db=3.0, q=1.0),
            HighShelfFilter(cutoff_frequency_hz=10000, gain_db=2.0),
            comp,
        ]
    else:
        chain += [
            gate,
            PeakFilter(cutoff_frequency_hz=3500, gain_db=1.8, q=1.0),
            HighShelfFilter(cutoff_frequency_hz=10000, gain_db=1.0),
            comp,
        ]
    return chain


def run(in_wav: Path, params: dict, ctx: OpContext) -> OpResult:
    params = params or {}
    deess = bool(params.get("deess", True))
    reduce_boom = bool(params.get("reduce_boom", True))
    stats = level_stats(in_wav)
    out = stage_wav(ctx, "enhance")
    tmp = ctx.work_dir / "enhance_pedalboard.wav"
    share = _DEES_SHARE if deess else 1.0
    board = Pedalboard(_plugins(
        params.get("preset", "voice"),
        stats["floor_dbfs"], stats["speech_dbfs"], reduce_boom,
    ))

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
                    done += data.shape[-1]
                    # no latency plugins → same frame count in and out
                    o.write(board.process(data, sr, reset=False))
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
