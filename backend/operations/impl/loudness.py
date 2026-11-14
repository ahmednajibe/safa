"""loudness — two-pass EBU R128 ``loudnorm`` with a true-peak cap (SPEC §2).

Pass 1 measures the input (to the null muxer, still progress-reporting);
pass 2 applies ``linear=true`` normalisation against those measured values
so the result is deterministic and sample-length preserving. When linear
gain would violate the true-peak target, loudnorm itself falls back to
dynamic and can land ~0.5 LU shy of the target — so the op re-measures the
result and applies one bounded corrective trim (``volume`` + ``alimiter``
at the TP ceiling) to pull it inside tolerance without breaking the cap.

When the input is so quiet that the target needs more than
``_MAX_GAIN_DB`` (+30 dB), the target is clamped to ``measured_I + 30``
and the op reports a message instead of a crazy gain (Review Focus 1).
Digital silence passes through unchanged — there is nothing to normalise.
"""
from __future__ import annotations

import math
import os
import re
import shutil
from pathlib import Path
from typing import Callable

from backend.media.ffmpeg_run import run_ffmpeg
from backend.operations.base import OpContext, OpResult, stage_wav
from backend.pipeline.types import CancelledError

_LRA = 11
_MAX_GAIN_DB = 30.0
_TRIM_TOLERANCE_LU = 0.25
QUIET_MESSAGE = "Very quiet recording — raised as far as safely possible"
_TARGETS = {"podcast": -16.0, "youtube": -14.0}
_MEASURE_KEYS = (
    "input_i", "input_tp", "input_lra", "input_thresh", "target_offset",
)


def _target_i(params: dict) -> float:
    kind = (params or {}).get("target", "podcast")
    if kind == "custom":
        return float((params or {}).get("lufs", -16))
    return _TARGETS.get(kind, -16.0)


def _measure(
    in_wav: Path,
    target_i: float,
    tp: float,
    ctx: OpContext,
    on_pct: Callable[[float], None] | None = None,
) -> dict:
    stderr = run_ffmpeg(
        [
            "-i", in_wav,
            "-af",
            f"loudnorm=I={target_i:g}:TP={tp:g}:LRA={_LRA}"
            ":print_format=json",
            "-f", "null", "-",
        ],
        duration_s=ctx.duration_s,
        on_pct=on_pct or (lambda p: None),
        should_cancel=ctx.should_cancel,
    )
    stats: dict[str, float] = {}
    for key in _MEASURE_KEYS:
        m = re.search(rf'"{key}"\s*:\s*"([^"]+)"', stderr)
        if m is None:
            raise RuntimeError(f"loudnorm pass 1 missing {key}")
        stats[key] = float(m.group(1))
    return stats


def run(in_wav: Path, params: dict, ctx: OpContext) -> OpResult:
    params = params or {}
    target_i = _target_i(params)
    tp = float(params.get("true_peak_db", -1))
    out = stage_wav(ctx, "loudness")

    stats = _measure(
        in_wav, target_i, tp, ctx,
        on_pct=lambda p: ctx.report(0.5 * p / 100.0, None),
    )
    measured_i = stats["input_i"]

    if not math.isfinite(measured_i):
        # Digital silence: nothing to raise — pass through untouched.
        if ctx.should_cancel():
            raise CancelledError()
        shutil.copyfile(in_wav, out)
        ctx.report(1.0, None)
        return OpResult(path=out, message=QUIET_MESSAGE)

    message = None
    if target_i - measured_i > _MAX_GAIN_DB:
        target_i = measured_i + _MAX_GAIN_DB
        message = QUIET_MESSAGE

    run_ffmpeg(
        [
            "-i", in_wav,
            "-af",
            f"loudnorm=I={target_i:g}:TP={tp:g}:LRA={_LRA}"
            f":measured_I={stats['input_i']:g}"
            f":measured_TP={stats['input_tp']:g}"
            f":measured_LRA={stats['input_lra']:g}"
            f":measured_thresh={stats['input_thresh']:g}"
            f":offset={stats['target_offset']:g}"
            ":linear=true:print_format=summary",
            "-ar", "48000",
            "-c:a", "pcm_f32le",
            out,
        ],
        duration_s=ctx.duration_s,
        on_pct=lambda p: ctx.report(0.5 + 0.45 * p / 100.0, None),
        should_cancel=ctx.should_cancel,
        out_path=out,
    )

    # Pass 2 may have gone dynamic under the TP cap and under/overshot by
    # a fraction of a LU: re-measure and trim with volume + a hard limiter
    # so the file lands on target while the ceiling stays honoured.
    post = _measure(out, target_i, tp, ctx)
    delta = target_i - post["input_i"]
    if abs(delta) > _TRIM_TOLERANCE_LU:
        limit_lin = 10.0 ** (tp / 20.0)
        tmp = ctx.work_dir / "stage_80_loudness_trim.wav"
        run_ffmpeg(
            [
                "-i", out,
                "-af",
                f"volume={delta:g}dB,"
                f"alimiter=limit={limit_lin:.4f}:level=false",
                "-ar", "48000",
                "-c:a", "pcm_f32le",
                tmp,
            ],
            duration_s=ctx.duration_s,
            on_pct=lambda p: ctx.report(0.95 + 0.05 * p / 100.0, None),
            should_cancel=ctx.should_cancel,
            out_path=tmp,
        )
        os.replace(tmp, out)
    ctx.report(1.0, None)
    return OpResult(path=out, message=message)
