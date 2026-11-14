"""highpass — rumble/DC removal (SPEC §2): two chained 2-pole butterworth
sections ≈ 24 dB/oct below ``cutoff_hz`` (registry default 75 Hz)."""
from __future__ import annotations

from pathlib import Path

from backend.media.ffmpeg_run import run_filter
from backend.operations.base import OpContext, OpResult, stage_wav


def run(in_wav: Path, params: dict, ctx: OpContext) -> OpResult:
    cutoff = float((params or {}).get("cutoff_hz", 75))
    # poles=2 twice in series → 24 dB/octave.
    afilter = (
        f"highpass=f={cutoff:g}:poles=2,"
        f"highpass=f={cutoff:g}:poles=2"
    )
    out = stage_wav(ctx, "highpass")
    run_filter(in_wav, out, afilter, ctx)
    return OpResult(path=out)
