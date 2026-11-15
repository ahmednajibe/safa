"""trim — keep only ``[start_s, end_s)`` of the input (SPEC §2, M4b Task 2).

The op composes ``ctx.timeline.keep([(start, end)])`` and — on video jobs —
snaps the new map to the frame grid (``ctx.fps``) so audio and video cut at
the same instants. Audio is rendered by ``render_keep`` with the kept spans
mapped back into input time.
"""
from __future__ import annotations

from pathlib import Path

import shutil

from backend.core.messages import msg
from backend.media.cutter import input_intervals, render_keep
from backend.media.timeline import Timeline
from backend.operations.base import OpContext, OpResult, stage_wav

_MSG = "Trim start must be before the end"


def run(in_wav: Path, params: dict, ctx: OpContext) -> OpResult:
    params = params or {}
    dur = ctx.duration_s
    # Preview jobs: the input wav is only the decoded preview region, but
    # start_s/end_s are still source-file times — shift by -preview_start
    # and clip to the decoded duration (M8 Task 0). A trim that misses the
    # preview entirely is a no-op, not an error; a trim that's invalid in
    # source terms stays an error.
    shift = ctx.preview[0] if ctx.preview else 0.0
    start_raw = float(params.get("start_s") or 0.0)
    end_raw = params.get("end_s")
    if end_raw is not None and not start_raw < float(end_raw):
        raise ValueError(_MSG)
    start = max(0.0, start_raw - shift)
    end = dur if end_raw is None else min(float(end_raw) - shift, dur)
    if not start < end:  # at/past the end of the decoded region
        if shift:
            out = stage_wav(ctx, "trim")
            shutil.copyfile(in_wav, out)
            ctx.report(1.0, None)
            text, code, p = msg("trim_outside_preview")
            return OpResult(
                path=out, message=text, message_code=code,
                message_params=p,
            )
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
