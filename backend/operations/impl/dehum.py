"""dehum — mains hum removal (SPEC §2): one narrow ``bandreject`` (Q=30) per
harmonic of ``base`` (50/60 Hz), chained; harmonics above 20 kHz skipped.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from backend.media.ffmpeg_run import run_filter
from backend.operations.base import OpContext, OpResult, stage_wav
from backend.pipeline.types import CancelledError

_NYQUIST_LIMIT_HZ = 20000.0


def run(in_wav: Path, params: dict, ctx: OpContext) -> OpResult:
    params = params or {}
    base = float(params.get("base", 50))
    harmonics = int(params.get("harmonics", 4))
    out = stage_wav(ctx, "dehum")

    notches = []
    for k in range(1, harmonics + 1):
        f = base * k
        if f > _NYQUIST_LIMIT_HZ:
            break
        notches.append(f"bandreject=f={f:g}:width_type=q:w=30")

    if not notches:
        if ctx.should_cancel():
            raise CancelledError()
        shutil.copyfile(in_wav, out)
        ctx.report(1.0, None)
        return OpResult(path=out)

    run_filter(in_wav, out, ",".join(notches), ctx)
    return OpResult(path=out)
