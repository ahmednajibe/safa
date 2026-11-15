"""Op interface (M4a): what the runner hands each op and what it gets back.

Every op is a ``OpFn``: ``run(in_wav, params, ctx) -> OpResult``. Input is
always a 48 kHz float32 WAV; the op writes its output WAV inside
``ctx.work_dir`` (conventionally ``stage_<order>_<op>.wav`` via
``stage_wav``) and returns it as ``OpResult.path``. Ops never read whole
files into RAM — they stream through ffmpeg or pedalboard blocks.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from backend.core.capabilities import Capabilities
from backend.media.timeline import Timeline
from backend.operations.registry import OPERATIONS
from backend.pipeline.types import JobDevice, OutputFile

# ``ctx.report(stage_pct 0..1 | None, message | None, message_code | None,
# message_params | None)`` — the runner turns it into a Progress row for
# the current stage. ``*msg(code, **kw)`` splats straight into args 2–4.
ReportFn = Callable[..., None]


@dataclass
class OpContext:
    work_dir: Path
    device: JobDevice
    duration_s: float
    sample_rate: int
    channels: int
    report: ReportFn
    should_cancel: Callable[[], bool]
    caps: Capabilities | None = None
    # Video frame rate of the job's source (None for audio-only jobs or
    # keep_video=False) — timeline ops snap cuts to this grid.
    fps: float | None = None
    # Timeline so far (source time → current output time); None means
    # identity. Length-changing ops return the composed map in OpResult.
    timeline: Timeline | None = None


@dataclass
class OpResult:
    path: Path
    actual_device: JobDevice = "cpu"
    fallback_reason: str | None = None
    message: str | None = None
    message_code: str | None = None
    message_params: dict | None = None
    warning_code: str | None = None
    warning_params: dict | None = None
    timeline: Timeline | None = None
    extra_outputs: list[OutputFile] = field(default_factory=list)


OpFn = Callable[[Path, dict, OpContext], OpResult]


def stage_wav(ctx: OpContext, op_id: str) -> Path:
    """Conventional stage output ``stage_<order>_<op>.wav`` (SPEC §3); the
    registry's fixed order number keeps files sorted in pipeline order."""
    spec = OPERATIONS.get(op_id)
    order = spec.order if spec else 99
    return ctx.work_dir / f"stage_{order:02d}_{op_id}.wav"
