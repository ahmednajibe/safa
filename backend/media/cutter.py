"""Sample-accurate keep-interval audio renderer (M4b plan Task 2).

``render_keep`` streams the kept spans of a 48 kHz WAV through
``soundfile`` seeks + block reads and writes them concatenated, applying a
raised-cosine fade-in at each segment's start and fade-out at its end —
**inside** the segment, never overlapping neighbours (SPEC §8). Output
length is exactly Σ kept lengths, which is what keeps the rendered audio
and the video cut list in sync.

``input_intervals`` maps a child timeline's kept source spans back into the
input file's time (the parent timeline's output time) so trim/silence ops
cut audio at the same instants the video renderer will.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import numpy as np
import soundfile as sf

from backend.media.timeline import Timeline
from backend.pipeline.types import CancelledError

_BLOCK = 1 << 16  # frames per streaming chunk (~2.7 s stereo f32 at 48 kHz)


def _sample_spans(
    keep: list[tuple[float, float]], sr: int, n_in: int
) -> list[tuple[int, int]]:
    """Keep intervals (seconds) → sorted, merged sample ranges."""
    spans = sorted(
        (max(0, round(a * sr)), min(n_in, round(b * sr)))
        for a, b in keep
    )
    merged: list[list[int]] = []
    for a, b in spans:
        if b <= a:
            continue
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged]


def render_keep(
    in_wav: Path,
    out_wav: Path,
    keep: list[tuple[float, float]],
    fade_ms: float = 10.0,
    report: Callable[[float, str | None], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> int:
    """Write the kept parts of ``in_wav`` to ``out_wav``; returns frames.

    ``keep`` are ``(start_s, end_s)`` intervals in the input file's time.
    Fade length per segment = ``min(fade_ms, segment_len / 4)`` so even very
    short kept islands get a boundary fade that stays inside them.
    """
    info = sf.info(str(in_wav))
    sr, channels, n_in = info.samplerate, info.channels, info.frames
    spans = _sample_spans(keep, sr, n_in)
    total = sum(b - a for a, b in spans)
    cancel = should_cancel or (lambda: False)
    emit = report or (lambda p, m=None: None)

    def _cancelled() -> None:
        raise CancelledError()

    written = 0
    try:
        with (
            sf.SoundFile(str(in_wav), "r") as fin,
            sf.SoundFile(
                str(out_wav), "w", samplerate=sr, channels=channels,
                format="WAV", subtype="FLOAT",
            ) as fout,
        ):
            for seg_start, seg_end in spans:
                seg_len = seg_end - seg_start
                fade = min(int(fade_ms * sr / 1000.0), seg_len // 4)
                fin.seek(seg_start)
                pos = 0
                while pos < seg_len:
                    if cancel():
                        _cancelled()
                    want = min(_BLOCK, seg_len - pos)
                    data = fin.read(want, dtype="float32", always_2d=True)
                    if len(data) == 0:
                        break  # unexpected EOF — stop cleanly
                    if fade > 0:
                        idx = pos + np.arange(len(data))
                        gain = np.ones(len(data), dtype=np.float32)
                        m_in = idx < fade
                        gain[m_in] = (
                            0.5 - 0.5 * np.cos(np.pi * idx[m_in] / fade)
                        )
                        m_out = idx >= seg_len - fade
                        gain[m_out] = (
                            0.5 + 0.5 * np.cos(
                                np.pi
                                * (idx[m_out] - (seg_len - fade))
                                / fade
                            )
                        )
                        data = data * gain[:, None]
                    fout.write(data)
                    pos += len(data)
                    written += len(data)
                    if total:
                        emit(min(1.0, written / total), None)
            if cancel():
                _cancelled()
    except CancelledError:
        Path(out_wav).unlink(missing_ok=True)
        raise
    emit(1.0, None)
    return written


def input_intervals(
    parent: Timeline, child: Timeline
) -> list[tuple[float, float]]:
    """Child timeline's kept spans expressed in parent-output (input) time.

    Rendering cuts the op's *input* file, whose clock is the parent
    timeline's output. Each child segment's source endpoints map back
    through ``parent``; snapped boundaries that land inside a removed gap
    clamp to the nearest kept point on the appropriate side.
    """
    spans = parent.kept_spans_src()
    out: list[tuple[float, float]] = []
    for seg in child.segments:
        a = _map_in(parent, spans, seg.src_start, forward=True)
        b = _map_in(parent, spans, seg.src_end, forward=False)
        if a is not None and b is not None and b - a > 0:
            out.append((a, b))
    return out


def _map_in(
    parent: Timeline,
    spans: list[tuple[float, float]],
    t: float,
    forward: bool,
) -> float | None:
    v = parent.src_to_out(t)
    if v is not None:
        return v
    if forward:
        nxt = parent.next_kept_src(t)
        return parent.src_to_out(nxt) if nxt is not None else None
    prev_end = None
    for s, e in spans:
        if e <= t:
            prev_end = e
    if prev_end is not None:
        return parent.src_to_out(prev_end)
    return parent.src_to_out(spans[0][0]) if spans else None
