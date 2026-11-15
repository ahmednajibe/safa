"""denoise — "Reduce background noise" (SPEC §2, M6 Task 1).

``engine="ai"`` → DeepFilterNet3 via ``backend.media.dfn.process_file``
(48 kHz only, chunked OLA, GPU-OOM → CPU retry); ``engine="classic"`` →
ffmpeg ``afftdn`` (CPU). The summary is the *measured* drop in the
non-speech noise floor (silero-VAD mask, like
``.orchestrator/analysis/nonspeech.py``) → ``noise_reduced{db}``.
"""
from __future__ import annotations

import dataclasses
import math
from pathlib import Path

from backend.core.messages import msg
from backend.media.ffmpeg_run import run_filter
from backend.media.silence_detect import non_speech_dbfs
from backend.operations.base import OpContext, OpResult, stage_wav

_STRENGTH_MAX = 40.0


def run(
    in_wav: Path, params: dict | None, ctx: OpContext
) -> OpResult:
    params = params or {}
    engine = params.get("engine", "ai")
    strength_db = min(float(params.get("strength_db", 24.0)), _STRENGTH_MAX)
    out = stage_wav(ctx, "denoise")
    requested = ctx.device

    # measure the floor under non-speech first so the summary is honest
    in_ns = non_speech_dbfs(
        in_wav,
        report=lambda p: ctx.report(0.05 * p, None),
        should_cancel=ctx.should_cancel,
    )

    if engine == "classic":
        sub = dataclasses.replace(
            ctx, report=lambda p, m: ctx.report(0.05 + 0.9 * p, m)
        )
        # afftdn: nr caps at ~40 dB. tn=1 (adaptive tracking) measurably
        # does nothing on stationary noise in this ffmpeg build, so nf is
        # seeded from the measured non-speech floor instead of a fixed
        # -50 (which leaves the noise untouched).
        nf = int(round(in_ns)) if math.isfinite(in_ns) else -50
        nf = max(-80, min(-20, nf))
        run_filter(
            in_wav,
            out,
            f"afftdn=nr={strength_db:g}:nf={nf}:tn=0",
            sub,
        )
        actual = "cpu"
        fallback = None
    else:
        from backend.media import dfn

        actual = dfn.process_file(
            in_wav,
            out,
            atten_lim_db=strength_db,
            device=requested,
            report=lambda p: ctx.report(0.05 + 0.9 * p, None),
            should_cancel=ctx.should_cancel,
        )
        fallback = (
            "GPU out of memory"
            if requested == "gpu" and actual == "cpu"
            else None
        )

    out_ns = non_speech_dbfs(
        out,
        report=lambda p: ctx.report(0.95 + 0.05 * p, None),
        should_cancel=ctx.should_cancel,
    )
    ctx.report(1.0, None)
    drop = (
        in_ns - out_ns
        if math.isfinite(in_ns) and math.isfinite(out_ns)
        else 0.0
    )
    text, code, p = msg("noise_reduced", db=int(round(drop)))
    return OpResult(
        path=out,
        actual_device=actual,
        fallback_reason=fallback,
        message=text,
        message_code=code,
        message_params=p,
    )
