"""Pure keep-interval planner for the silence op (SPEC §10, M5 Task 3,
M6b Task 3).

``plan_keep`` turns detected speech intervals into the keep list the
cutter renders: speech padded by ``pad_s``, gaps shorter than
``min_silence_s`` kept (words never crash together), longer gaps removed
(default) or shortened to ``keep_pause_s``. Remove mode also drops every
leading/trailing silence; shorten keeps at most ``keep_pause_s`` of them.

The ``min_silence_s`` decision is made on the **raw** gap between speech
intervals, before padding is applied — padding first under-reports gaps
(a 0.4 s raw gap with ``pad_s`` 0.1 shrinks to 0.2 s on the padded
blocks, so a 0.3 s threshold would wrongly keep it). Pads are then added
only where the gap will actually be cut.

``breaths`` (optional, remove mode only) lists detected breath spans:
when a breath lies inside a pad zone, the pad is clamped at the breath
edge (trailing pad ends at the breath start, leading pad starts at the
breath end) so a splice never keeps a fragment of gasp.

Empty speech returns ``[(0, duration)]`` — the op maps that to the
``no_speech_found`` message; nothing is ever rendered empty.
"""
from __future__ import annotations

from typing import Literal, Optional


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
    breaths: Optional[list[tuple[float, float]]] = None,
) -> list[tuple[float, float]]:
    """Keep intervals in input-file time, sorted and inside [0, duration]."""
    if not speech or duration <= 0:
        return [(0.0, max(0.0, duration))]

    raw = _merge(speech)
    if not raw:
        return [(0.0, duration)]

    breaths = sorted(breaths or []) if action == "remove" else []

    def _trail_pad(b: float) -> float:
        """Keep-edge after a speech end: pad into the cut gap, stopping
        at any breath that starts inside the pad zone."""
        end = min(duration, b + pad_s)
        for bs, _be in breaths:
            if b <= bs < end:
                end = bs
                break
        return end

    def _lead_pad(a: float) -> float:
        """Keep-edge before a speech start: pad into the cut gap, but
        start after any breath ending inside the pad zone."""
        start = max(0.0, a - pad_s)
        for _bs, be in breaths:
            if start < be <= a:
                start = be
        return start

    # Group raw intervals linked by raw gaps < min_silence_s — those
    # gaps are kept whole, so each group becomes one padded block. The
    # group edges face either a genuinely cut pause or the file edges.
    groups: list[list[tuple[float, float]]] = [[raw[0]]]
    for iv in raw[1:]:
        if iv[0] - groups[-1][-1][1] < min_silence_s:
            groups[-1].append(iv)
        else:
            groups.append([iv])

    blocks = [
        (_lead_pad(g[0][0]), _trail_pad(g[-1][1])) for g in groups
    ]
    blocks = [(a, b) for a, b in blocks if b > a]
    if not blocks:
        return [(0.0, duration)]

    keep: list[tuple[float, float]] = []

    # Leading silence. Shorten keeps max(pad_s, keep_pause_s) of it in
    # total — the pad already inside the first block counts toward the
    # budget (R1 B5).
    first_start = blocks[0][0]
    if first_start > 0 and action == "shorten":
        extra = min(first_start, max(0.0, keep_pause_s - pad_s))
        if extra > 0.0:
            keep.append((first_start - extra, first_start))

    # Blocks with the pause policy applied between them. Every
    # inter-block gap is a real pause by construction (raw gap >=
    # min_silence_s): remove drops it, shorten keeps a pause of
    # min(raw_gap, max(2·pad_s, keep_pause_s)) split half/half against
    # the padded edges — the 2·pad inside the blocks counts toward the
    # budget, so an exact keep_pause_s of *audible* silence survives.
    keep.append(blocks[0])
    for a, b in blocks[1:]:
        prev_end = keep[-1][1]
        if action == "shorten":
            half = max(
                0.0, min(a - prev_end, keep_pause_s - 2.0 * pad_s)
            ) / 2.0
            keep[-1] = (keep[-1][0], prev_end + half)
            keep.append((a - half, b))
        else:
            keep.append((a, b))

    # Trailing silence — same edge rule as the leading silence.
    last_end = keep[-1][1]
    if last_end < duration and action == "shorten":
        extra = min(duration - last_end, max(0.0, keep_pause_s - pad_s))
        if extra > 0.0:
            keep.append((last_end, last_end + extra))

    return _merge(keep)
