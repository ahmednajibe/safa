"""Pure keep-interval planner for the silence op (SPEC §10, M5 Task 3).

``plan_keep`` turns detected speech intervals into the keep list the
cutter renders: speech padded by ``pad_s``, gaps shorter than
``min_silence_s`` kept (words never crash together), longer gaps removed
(default) or shortened to ``keep_pause_s``. Remove mode also drops every
leading/trailing silence; shorten keeps at most ``keep_pause_s`` of them.

Empty speech returns ``[(0, duration)]`` — the op maps that to the
``no_speech_found`` message; nothing is ever rendered empty.
"""
from __future__ import annotations

from typing import Literal


def _merge(blocks: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Sort + merge overlapping/touching blocks."""
    spans = sorted((a, b) for a, b in blocks if b > a)
    out: list[list[float]] = []
    for a, b in spans:
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def plan_keep(
    speech: list[tuple[float, float]],
    duration: float,
    action: Literal["remove", "shorten"],
    min_silence_s: float,
    pad_s: float,
    keep_pause_s: float,
) -> list[tuple[float, float]]:
    """Keep intervals in input-file time, sorted and inside [0, duration]."""
    if not speech or duration <= 0:
        return [(0.0, max(0.0, duration))]

    blocks = _merge(
        [
            (
                max(0.0, a - pad_s),
                min(duration, b + pad_s),
            )
            for a, b in speech
        ]
    )
    if not blocks:
        return [(0.0, duration)]

    keep: list[tuple[float, float]] = []

    # Leading silence.
    first_start = blocks[0][0]
    if first_start > 0:
        if action == "shorten":
            keep.append((max(0.0, first_start - keep_pause_s), first_start))

    # Blocks with the gap policy applied between them.
    keep.append(blocks[0])
    for a, b in blocks[1:]:
        prev_end = keep[-1][1]
        gap = a - prev_end
        if gap < min_silence_s:
            keep[-1] = (keep[-1][0], b)  # too short to be a pause → keep
        elif action == "shorten":
            half = keep_pause_s / 2.0
            keep[-1] = (keep[-1][0], prev_end + half)
            keep.append((a - half, b))
        else:
            keep.append((a, b))

    # Trailing silence.
    last_end = keep[-1][1]
    if last_end < duration and action == "shorten":
        keep.append((last_end, min(duration, last_end + keep_pause_s)))

    return _merge(keep)
