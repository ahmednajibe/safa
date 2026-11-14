"""enhance — voice polish (SPEC §2): pedalboard studio chain streamed in
10 s blocks (``reset=False`` keeps filter/comp state across blocks), then an
optional ffmpeg ``deesser`` pass.

Presets (registry ``preset`` param):
- ``voice``: HPF 80 Hz → −2 dB @250 Hz → +3 dB @3.5 kHz → +1.5 dB shelf @10k
  → compressor (−18 dB, 3:1, 5 ms/80 ms) → +2 dB.
- ``bright``: voice with +4 dB presence and +3 dB shelf.
- ``warm``: +1.5 dB @150 Hz (no 250 Hz dip), +1.5 dB presence, no shelf.
"""
from __future__ import annotations

import dataclasses
import os
from pathlib import Path

from pedalboard import (
    Compressor,
    Gain,
    HighShelfFilter,
    HighpassFilter,
    PeakFilter,
    Pedalboard,
)
from pedalboard.io import AudioFile

from backend.media.ffmpeg_run import run_filter
from backend.operations.base import OpContext, OpResult, stage_wav
from backend.pipeline.types import CancelledError

_BLOCK_S = 10.0
_DEES_SHARE = 0.8  # pedalboard pass covers 0..80 % when the deesser follows
_DEESER = "deesser=i=0.4:m=0.5:f=0.5"


def _plugins(preset: str) -> list:
    comp = Compressor(
        threshold_db=-18, ratio=3, attack_ms=5, release_ms=80
    )
    makeup = Gain(gain_db=2)
    if preset == "bright":
        return [
            HighpassFilter(cutoff_frequency_hz=80),
            PeakFilter(cutoff_frequency_hz=250, gain_db=-2, q=1.0),
            PeakFilter(cutoff_frequency_hz=3500, gain_db=4, q=1.0),
            HighShelfFilter(cutoff_frequency_hz=10000, gain_db=3),
            comp, makeup,
        ]
    if preset == "warm":
        return [
            HighpassFilter(cutoff_frequency_hz=80),
            PeakFilter(cutoff_frequency_hz=150, gain_db=1.5, q=1.0),
            PeakFilter(cutoff_frequency_hz=3500, gain_db=1.5, q=1.0),
            comp, makeup,
        ]
    return [  # voice (default and fallback for unknown presets)
        HighpassFilter(cutoff_frequency_hz=80),
        PeakFilter(cutoff_frequency_hz=250, gain_db=-2, q=1.0),
        PeakFilter(cutoff_frequency_hz=3500, gain_db=3, q=1.0),
        HighShelfFilter(cutoff_frequency_hz=10000, gain_db=1.5),
        comp, makeup,
    ]


def run(in_wav: Path, params: dict, ctx: OpContext) -> OpResult:
    params = params or {}
    deess = bool(params.get("deess", True))
    out = stage_wav(ctx, "enhance")
    tmp = ctx.work_dir / "enhance_pedalboard.wav"
    share = _DEES_SHARE if deess else 1.0
    board = Pedalboard(_plugins(params.get("preset", "voice")))

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
