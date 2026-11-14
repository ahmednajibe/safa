"""declick — click/pop removal via ffmpeg ``adeclick`` (SPEC §2).

Sensitivity mapping (UI select → adeclick params):
- ``gentle``: ``w=55:o=75:t=4`` — defaults with a *higher* threshold (t=4),
  so only obvious clicks are touched.
- ``normal``: ``t=2`` — ffmpeg's default detection threshold.
- ``strong``: ``t=1.5:b=4`` — lower threshold (catches more) plus stronger
  burst fusion for dense crackle.
"""
from __future__ import annotations

from pathlib import Path

from backend.media.ffmpeg_run import run_filter
from backend.operations.base import OpContext, OpResult, stage_wav

_MAP = {
    "gentle": "adeclick=w=55:o=75:t=4",
    "normal": "adeclick=t=2",
    "strong": "adeclick=t=1.5:b=4",
}


def run(in_wav: Path, params: dict, ctx: OpContext) -> OpResult:
    mode = (params or {}).get("sensitivity", "normal")
    afilter = _MAP.get(mode, _MAP["normal"])
    out = stage_wav(ctx, "declick")
    run_filter(in_wav, out, afilter, ctx)
    return OpResult(path=out)
