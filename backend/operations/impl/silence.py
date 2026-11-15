"""silence — "Cut silence" (SPEC §10, M5 Task 4): remove the silent parts.

Default action ``remove`` deletes every silent span ≥ ``min_silence_s``
(including leading/trailing silence); ``shorten`` leaves ``keep_pause_s``
of each long pause. Speech detection is Silero VAD (``mode="vad"``,
always CPU) or a plain volume threshold (``mode="amplitude"``, which also
works on non-speech audio like karaoke output).

The op plans keep-intervals in input time, composes them into the job
timeline (snapped to the video frame grid on video jobs) and renders the
audio with the M4b sample-accurate cutter — audio and video cut at the
same instants by construction. A file with no detected speech passes
through unchanged with the ``no_speech_found`` message — never empty.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from backend.core.messages import msg
from backend.media.cutter import input_intervals, render_keep
from backend.media.silence_detect import (
    speech_intervals_amplitude,
    speech_intervals_vad,
)
from backend.media.silence_plan import plan_keep
from backend.media.timeline import Timeline
from backend.operations.base import OpContext, OpResult, stage_wav


def _result(out: Path, code: str, params: dict, timeline=None) -> OpResult:
    text, c, p = msg(code, **params)
    return OpResult(
        path=out, actual_device="cpu",
        message=text, message_code=c, message_params=p,
        timeline=timeline,
    )


def run(in_wav: Path, params: dict, ctx: OpContext) -> OpResult:
    params = params or {}
    action = params.get("action", "remove")
    mode = params.get("mode", "vad")
    min_silence_s = float(params.get("min_silence_s", 0.3))
    pad_s = float(params.get("pad_s", 0.1))
    keep_pause_s = float(params.get("keep_pause_s", 0.25))
    threshold_db = float(params.get("threshold_db", -40))
    dur = ctx.duration_s
    out = stage_wav(ctx, "silence")

    # Detection → 0–60 % of the stage (render reports 60–100 %).
    detect_report = lambda p: ctx.report(0.6 * p, None)  # noqa: E731
    if mode == "amplitude":
        speech = speech_intervals_amplitude(
            in_wav, threshold_db=threshold_db,
            min_silence_s=min_silence_s,
            report=detect_report, should_cancel=ctx.should_cancel,
        )
    else:
        speech = speech_intervals_vad(
            in_wav,
            report=detect_report, should_cancel=ctx.should_cancel,
        )

    if not speech:
        # Never render empty (M5 constraint): file passes through with a
        # hint that Volume-threshold mode may still find pauses.
        shutil.copyfile(in_wav, out)
        ctx.report(1.0, None)
        return _result(out, "no_speech_found", {})

    keep = plan_keep(
        speech, dur, action, min_silence_s, pad_s, keep_pause_s
    )
    kept_total = sum(b - a for a, b in keep)
    if dur - kept_total <= 1e-3:
        # Nothing to cut — pass through; the summary still reports 0 so
        # the UI can say "nothing was removed".
        shutil.copyfile(in_wav, out)
        ctx.report(1.0, None)
        code = "silence_removed" if action == "remove" else "pauses_shortened"
        return _result(out, code, {"seconds": 0.0, "count": 0})

    parent = ctx.timeline or Timeline.identity(dur)
    new_tl = parent.keep(keep)
    if ctx.fps:
        new_tl = new_tl.snap(ctx.fps)
    if not new_tl.segments:
        # Frame snapping erased everything — treat as "nothing to cut"
        # rather than emitting a broken file.
        shutil.copyfile(in_wav, out)
        ctx.report(1.0, None)
        code = "silence_removed" if action == "remove" else "pauses_shortened"
        return _result(out, code, {"seconds": 0.0, "count": 0})

    render_intervals = input_intervals(parent, new_tl)
    render_keep(
        in_wav, out, render_intervals,
        report=lambda p, m=None: ctx.report(0.6 + 0.4 * p, m),
        should_cancel=ctx.should_cancel,
    )
    ctx.report(1.0, None)

    # Summary over what this op actually dropped, in input time and after
    # snapping: "places" = removed spans between/around the kept parts.
    removed_s = dur - sum(b - a for a, b in render_intervals)
    cut_count = 0
    pos = 0.0
    for a, b in render_intervals:
        if a - pos > 1e-3:
            cut_count += 1
        pos = max(pos, b)
    if dur - pos > 1e-3:
        cut_count += 1
    code = "pauses_shortened" if action == "shorten" else "silence_removed"
    return _result(
        out, code, {"seconds": round(removed_s, 1), "count": cut_count},
        timeline=new_tl,
    )
