"""trim — keep only ``[start_s, end_s)`` of the input (SPEC §2, M4b Task 2).

The op composes ``ctx.timeline.keep([(start, end)])`` and — on video jobs —
snaps the new map to the frame grid (``ctx.fps``) so audio and video cut at
the same instants. Audio is rendered by ``render_keep`` with the kept spans
mapped back into input time.
"""
from __future__ import annotations

from pathlib import Path

from backend.media.cutter import input_intervals, render_keep
from backend.media.timeline import Timeline
from backend.operations.base import OpContext, OpResult, stage_wav

_MSG = "Trim start must be before the end"


def run(in_wav: Path, params: dict, ctx: OpContext) -> OpResult:
    params = params or {}
    dur = ctx.duration_s
    start = max(0.0, float(params.get("start_s") or 0.0))
    end_raw = params.get("end_s")
    end = dur if end_raw is None else min(float(end_raw), dur)
    if not start < end:  # start ≥ end, or start at/past the file's end
        raise ValueError(_MSG)

    parent = ctx.timeline or Timeline.identity(dur)
    new_tl = parent.keep([(start, end)])
    if ctx.fps:
        new_tl = new_tl.snap(ctx.fps)
    if not new_tl.segments:
        raise ValueError(_MSG)

    out = stage_wav(ctx, "trim")
    render_keep(
        in_wav,
        out,
        input_intervals(parent, new_tl),
        report=lambda p, m=None: ctx.report(p, m),
        should_cancel=ctx.should_cancel,
    )
    return OpResult(path=out, timeline=new_tl)
