"""Timeline segment map (SPEC §8, M4b plan Task 1) — pure math, no I/O.

A ``Timeline`` maps **source time** (seconds in the decoded original) to
**output time** (seconds in the rendered result) through a sorted list of
kept ``Segment``s. Each segment says: play ``[src_start, src_end)`` of the
source at ``speed``, starting at output position ``out_start``. Output time
is contiguous — every output instant maps back into exactly one segment —
while source time may fall into removed gaps.

Composability (trim → silence → speed): ``keep()`` takes intervals in the
*current* timeline's output time and rewrites the map into source
coordinates, so M5 can feed hundreds of keep-intervals and still render one
cut list. ``with_speed()`` composes a global tempo change on top.

The runner feeds this single map to three consumers: the sample-accurate
audio cutter, the video select/setpts filtergraph, and ``timeline.json``
for the UI's A/B playhead translation + subtitle timing.
"""
from __future__ import annotations

from dataclasses import dataclass

_EPS = 1e-9


@dataclass(frozen=True)
class Segment:
    """One kept source span placed on the output timeline."""

    src_start: float
    src_end: float
    out_start: float
    speed: float = 1.0

    @property
    def out_end(self) -> float:
        return self.out_start + (self.src_end - self.src_start) / self.speed


@dataclass(frozen=True)
class Timeline:
    source_duration: float
    segments: tuple[Segment, ...]

    # ---- construction ------------------------------------------------------

    @classmethod
    def identity(cls, duration: float) -> "Timeline":
        """1:1 map — nothing removed, speed 1."""
        return cls(float(duration), (Segment(0.0, float(duration), 0.0),))

    @property
    def is_identity(self) -> bool:
        return (
            len(self.segments) == 1
            and abs(self.segments[0].src_start) <= _EPS
            and abs(self.segments[0].src_end - self.source_duration) <= _EPS
            and abs(self.segments[0].out_start) <= _EPS
            and abs(self.segments[0].speed - 1.0) <= _EPS
        )

    @property
    def out_duration(self) -> float:
        return self.segments[-1].out_end if self.segments else 0.0

    # ---- composition ---------------------------------------------------------

    def keep(self, intervals_out: list[tuple[float, float]]) -> "Timeline":
        """Keep only ``intervals_out`` (in this timeline's output time).

        Returns the composed timeline still expressed in source time: a kept
        output window inside a segment maps back through that segment's
        speed. Intervals are clipped to ``[0, out_duration]``, sorted, and
        merged where they overlap or touch.
        """
        spans = _normalize(intervals_out, self.out_duration)
        segs: list[Segment] = []
        out_pos = 0.0
        for a, b in spans:
            for seg in self.segments:
                lo = max(a, seg.out_start)
                hi = min(b, seg.out_end)
                if hi - lo <= _EPS:
                    continue
                src_lo = seg.src_start + (lo - seg.out_start) * seg.speed
                src_hi = seg.src_start + (hi - seg.out_start) * seg.speed
                segs.append(
                    Segment(src_lo, src_hi, out_pos, seg.speed)
                )
                out_pos += segs[-1].out_end - out_pos
        return Timeline(self.source_duration, tuple(segs))

    def with_speed(self, factor: float) -> "Timeline":
        """Compose a global speed change on top of the current map."""
        f = float(factor)
        if f <= 0:
            raise ValueError("speed factor must be positive")
        return Timeline(
            self.source_duration,
            tuple(
                Segment(s.src_start, s.src_end, s.out_start / f, s.speed * f)
                for s in self.segments
            ),
        )

    # ---- time translation -----------------------------------------------------

    def src_to_out(self, t: float) -> float | None:
        """Source time → output time; ``None`` when ``t`` was cut."""
        if t < -_EPS or t > self.source_duration + _EPS:
            return None
        for seg in self.segments:
            if t < seg.src_start - _EPS:
                return None  # inside a removed gap
            if t <= seg.src_end + _EPS:
                src = min(max(t, seg.src_start), seg.src_end)
                return seg.out_start + (src - seg.src_start) / seg.speed
        return None

    def out_to_src(self, t: float) -> float:
        """Output time → source time (clamped to the kept range).

        At a boundary between two kept segments the following segment wins:
        output instant ``t`` plays the *next* segment's first source frame.
        """
        if not self.segments:
            return 0.0
        if t <= 0.0:
            return self.segments[0].src_start
        for seg in self.segments:
            if t < seg.out_end - _EPS:
                src = seg.src_start + (t - seg.out_start) * seg.speed
                return min(max(src, seg.src_start), seg.src_end)
        return self.segments[-1].src_end

    def next_kept_src(self, t: float) -> float | None:
        """Nearest kept source time ≥ ``t``; ``None`` past the last segment.

        Inside a kept span this is ``t`` itself; inside a removed span it is
        the next segment's ``src_start`` — where playback resumes.
        """
        for seg in self.segments:
            if t <= seg.src_end + _EPS:
                return max(t, seg.src_start)
        return None

    # ---- spans ----------------------------------------------------------------

    def kept_spans_src(self) -> list[tuple[float, float]]:
        """Kept source intervals; touching spans are merged."""
        out: list[list[float]] = []
        for seg in self.segments:
            if out and seg.src_start - out[-1][1] <= _EPS:
                out[-1][1] = max(out[-1][1], seg.src_end)
            else:
                out.append([seg.src_start, seg.src_end])
        return [(a, b) for a, b in out]

    def removed_spans(self) -> list[tuple[float, float]]:
        """Removed source intervals (the gaps between kept spans)."""
        out: list[tuple[float, float]] = []
        pos = 0.0
        for a, b in self.kept_spans_src():
            if a - pos > _EPS:
                out.append((pos, a))
            pos = max(pos, b)
        if self.source_duration - pos > _EPS:
            out.append((pos, self.source_duration))
        return out

    # ---- frame snapping ---------------------------------------------------------

    def snap(self, fps: float) -> "Timeline":
        """Snap segment boundaries to the video frame grid (source time).

        Each boundary rounds to ``round(t*fps)/fps``; segments collapsing to
        less than one frame are dropped. Output positions are rebuilt so the
        map stays contiguous.
        """
        if fps <= 0:
            return self
        q = 1.0 / fps
        segs: list[Segment] = []
        out_pos = 0.0
        for seg in self.segments:
            a = round(seg.src_start * fps) * q
            b = round(seg.src_end * fps) * q
            a = min(max(a, 0.0), self.source_duration)
            b = min(max(b, 0.0), self.source_duration)
            if b - a < q - _EPS:
                continue  # shorter than one frame after snapping
            segs.append(Segment(a, b, out_pos, seg.speed))
            out_pos += segs[-1].out_end - out_pos
        return Timeline(self.source_duration, tuple(segs))

    # ---- serialization ------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "source_duration": self.source_duration,
            "segments": [
                {
                    "src_start": s.src_start,
                    "src_end": s.src_end,
                    "out_start": s.out_start,
                    "out_end": s.out_end,
                    "speed": s.speed,
                }
                for s in self.segments
            ],
            "removed": [[a, b] for a, b in self.removed_spans()],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Timeline":
        segs = tuple(
            Segment(
                float(s["src_start"]),
                float(s["src_end"]),
                float(s["out_start"]),
                float(s.get("speed", 1.0)),
            )
            for s in d.get("segments") or []
        )
        return cls(float(d.get("source_duration") or 0.0), segs)


def _normalize(
    intervals: list[tuple[float, float]], limit: float
) -> list[tuple[float, float]]:
    """Clip to ``[0, limit]``, drop empties, sort, merge overlap/touch."""
    spans = sorted(
        (max(0.0, a), min(b, limit))
        for a, b in ((float(lo), float(hi)) for lo, hi in intervals)
        if b > 0.0 and a < limit
    )
    spans = [(a, b) for a, b in spans if b - a > _EPS]
    merged: list[list[float]] = []
    for a, b in spans:
        if merged and a - merged[-1][1] <= _EPS:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged]
