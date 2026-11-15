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

import soundfile as sf

from backend.core.messages import msg
from backend.media.cutter import input_intervals, render_keep
from backend.media.silence_detect import (
    speech_intervals_amplitude,
    speech_intervals_vad,
)
from backend.media.silence_plan import plan_keep
from backend.media.timeline import Timeline
from backend.operations.base import OpContext, OpResult, stage_wav


def _breaths_near_splices(
    in_wav: Path,
    speech: list[tuple[float, float]],
    duration: float,
    min_silence_s: float,
    margin_s: float = 1.0,
    should_cancel=None,
) -> list[tuple[float, float]]:
    """Breath spans detected in bounded windows around splice points.

    The pad clamp only applies to breaths inside pad zones — the pad_s
    slivers at the edges of cut gaps and at the file edges. Scanning
    ``[gap − margin, gap + margin]`` per cuttable gap (merged when they
    overlap) finds every breath that can reach a pad zone while reading
    only a few seconds of audio at a time.
    """
    from backend.media.breaths import (
        detect_breaths, whole_file_level_stats,
    )

    edges: list[tuple[float, float]] = []
    if speech[0][0] > 0:
        edges.append((speech[0][0], speech[0][0]))  # leading pad zone
    for (_, b0), (a1, _) in zip(speech, speech[1:]):
        if a1 - b0 >= min_silence_s:
            edges.append((b0, a1))
    if duration - speech[-1][1] > 0:
        edges.append((speech[-1][1], speech[-1][1]))  # trailing pad zone
    if not edges:
        return []

    # Merge overlapping scan windows; each covers its gap plus enough
    # margin to catch a breath reaching into either pad zone.
    zones: list[list[float]] = []
    for a, b in edges:
        za = max(0.0, a - margin_s)
        zb = min(duration, b + margin_s)
        if zones and za <= zones[-1][1]:
            zones[-1][1] = max(zones[-1][1], zb)
        else:
            zones.append([za, zb])

    # R1 B9: the level gate must be calibrated on the WHOLE file — inside
    # a ±1 s window beside a > ~11 s pause the P85 lands on silence and
    # every zone returns [].
    level_stats = whole_file_level_stats(
        in_wav, speech, should_cancel
    )

    spans: list[list[float]] = []
    with sf.SoundFile(str(in_wav), "r") as fin:
        sr = fin.samplerate
        for za, zb in zones:
            if should_cancel is not None and should_cancel():
                from backend.pipeline.types import CancelledError

                raise CancelledError()
            fin.seek(int(za * sr))
            blk = fin.read(
                int((zb - za) * sr), dtype="float32", always_2d=True
            )
            if not len(blk):
                continue
            for s, e in detect_breaths(
                blk.mean(axis=1), sr, level_stats=level_stats
            ):
                spans.append([za + s, za + e])
    spans.sort()
    merged: list[list[float]] = []
    for s, e in spans:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return [(s, e) for s, e in merged]


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

    breaths = None
    if action == "remove" and mode == "vad" and pad_s > 0:
        # Breath-aware pads: a breath inside a pad zone would survive the
        # cut as a half-gasp at the splice — clamp the pad at its edge.
        # Only pad zones can leak gasps, so detection scans bounded
        # windows around cuttable gaps + file edges (~±1 s each), never
        # the whole file — RAM stays flat on long inputs.
        breaths = _breaths_near_splices(
            in_wav, speech, dur, min_silence_s,
            should_cancel=ctx.should_cancel,
        )

    keep = plan_keep(
        speech, dur, action, min_silence_s, pad_s, keep_pause_s,
        breaths=breaths,
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
        # R1 B7: snapped edges must not grow past the parent coverage —
        # the video renderer uses this map verbatim.
        new_tl = new_tl.snap(ctx.fps).keep_src(parent.kept_spans_src())
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
