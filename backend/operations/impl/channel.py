"""channel — keep stereo / combine to mono / pick one side (SPEC §2).

``keep`` and any mode on an already-mono input are a plain file copy (no
re-process); stereo → mono/side goes through ffmpeg ``pan``.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from backend.media.ffmpeg_run import run_filter
from backend.operations.base import OpContext, OpResult, stage_wav
from backend.pipeline.types import CancelledError

_PAN = {
    "mono": "pan=mono|c0=0.5*c0+0.5*c1",
    "left": "pan=mono|c0=c0",
    "right": "pan=mono|c0=c1",
}


def run(in_wav: Path, params: dict, ctx: OpContext) -> OpResult:
    params = params or {}
    mode = params.get("mode", "keep")
    out = stage_wav(ctx, "channel")
    if mode == "keep" or ctx.channels == 1 or mode not in _PAN:
        # Nothing to mix down: copy through (mono input with mono/left/right
        # is unchanged per plan; an unknown mode is a safe no-op).
        if ctx.should_cancel():
            raise CancelledError()
        shutil.copyfile(in_wav, out)
        ctx.report(1.0, None)
        return OpResult(path=out)
    run_filter(in_wav, out, _PAN[mode], ctx)
    return OpResult(path=out)
