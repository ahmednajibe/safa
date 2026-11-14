"""M4b Task 1: timeline segment-map math (SPEC §8, plan Task 1).

Times are seconds; a Timeline maps source time to output time through kept
segments so audio cutting, video rendering, subtitles and the A/B playhead
all agree. ``keep()`` intervals are expressed in the *current* timeline's
output time and compose back to source coordinates.
"""
import random

import pytest

from backend.media.timeline import Segment, Timeline

EPS = 1e-6


def test_identity():
    tl = Timeline.identity(10.0)
    assert tl.is_identity
    assert tl.out_duration == pytest.approx(10.0)
    assert tl.src_to_out(4.2) == pytest.approx(4.2)
    assert tl.out_to_src(4.2) == pytest.approx(4.2)
    assert tl.removed_spans() == []
    assert tl.kept_spans_src() == [(0.0, 10.0)]
    assert tl.next_kept_src(3.0) == pytest.approx(3.0)


def test_segment_out_end():
    s = Segment(src_start=2.0, src_end=8.0, out_start=1.0, speed=1.5)
    assert s.out_end == pytest.approx(1.0 + 6.0 / 1.5)


def test_keep_single_interval_is_trim():
    tl = Timeline.identity(10.0).keep([(2.0, 7.0)])
    assert not tl.is_identity
    assert tl.out_duration == pytest.approx(5.0)
    assert tl.kept_spans_src() == [(2.0, 7.0)]
    assert tl.removed_spans() == [(0.0, 2.0), (7.0, 10.0)]
    # source mapping
    assert tl.src_to_out(2.0) == pytest.approx(0.0)
    assert tl.src_to_out(4.5) == pytest.approx(2.5)
    assert tl.src_to_out(7.0) == pytest.approx(5.0)
    assert tl.src_to_out(1.0) is None
    assert tl.src_to_out(7.5) is None
    # output mapping
    assert tl.out_to_src(0.0) == pytest.approx(2.0)
    assert tl.out_to_src(2.5) == pytest.approx(4.5)
    assert tl.out_to_src(5.0) == pytest.approx(7.0)
    # next kept boundary
    assert tl.next_kept_src(0.0) == pytest.approx(2.0)
    assert tl.next_kept_src(1.5) == pytest.approx(2.0)
    assert tl.next_kept_src(7.5) is None


def test_keep_after_trim_composes_to_source():
    tl = Timeline.identity(10.0).keep([(1.0, 8.0)])  # out = src - 1
    tl2 = tl.keep([(2.0, 5.0)])  # output-window → src (3,6)
    assert tl2.source_duration == pytest.approx(10.0)
    assert tl2.kept_spans_src() == [(3.0, 6.0)]
    assert tl2.out_duration == pytest.approx(3.0)
    assert tl2.src_to_out(3.0) == pytest.approx(0.0)
    assert tl2.src_to_out(6.0) == pytest.approx(3.0)


def test_speed_after_keep():
    tl = Timeline.identity(10.0).keep([(1.0, 8.0)]).with_speed(1.5)
    assert tl.out_duration == pytest.approx(7.0 / 1.5)
    assert tl.src_to_out(2.5) == pytest.approx(1.5 / 1.5)
    assert tl.out_to_src(2.0) == pytest.approx(1.0 + 2.0 * 1.5)
    assert tl.out_to_src(tl.out_duration) == pytest.approx(8.0)


def test_speed_on_identity():
    tl = Timeline.identity(9.0).with_speed(0.75)
    assert tl.out_duration == pytest.approx(12.0)
    assert not tl.is_identity
    assert tl.src_to_out(4.5) == pytest.approx(6.0)


def test_keep_after_speed_composes():
    # identity 20 → speed 2 → out 10 → keep out (1, 4) → src (2, 8)
    tl = Timeline.identity(20.0).with_speed(2.0).keep([(1.0, 4.0)])
    assert tl.kept_spans_src() == [(2.0, 8.0)]
    assert tl.out_duration == pytest.approx(3.0)
    assert tl.src_to_out(5.0) == pytest.approx(1.5)


def test_keep_multiple_intervals():
    tl = Timeline.identity(10.0).keep([(1.0, 3.0), (6.0, 8.0), (4.5, 5.0)])
    assert tl.out_duration == pytest.approx(2.0 + 0.5 + 2.0)
    assert tl.kept_spans_src() == [(1.0, 3.0), (4.5, 5.0), (6.0, 8.0)]
    assert tl.removed_spans() == [
        (0.0, 1.0), (3.0, 4.5), (5.0, 6.0), (8.0, 10.0)
    ]
    # junction: out t=2.0 is the end of seg1 / start of seg2 → src of next seg
    assert tl.out_to_src(2.0) == pytest.approx(4.5)
    assert tl.out_to_src(2.0 - EPS) == pytest.approx(3.0 - EPS)
    assert tl.src_to_out(3.0) == pytest.approx(2.0)
    assert tl.src_to_out(4.49) is None
    assert tl.next_kept_src(3.5) == pytest.approx(4.5)


def test_keep_clips_and_sorts_intervals():
    tl = Timeline.identity(10.0).keep([(8.0, 99.0), (-5.0, 2.0)])
    assert tl.kept_spans_src() == [(0.0, 2.0), (8.0, 10.0)]
    assert tl.out_duration == pytest.approx(4.0)


def test_keep_empty():
    tl = Timeline.identity(10.0).keep([])
    assert tl.out_duration == pytest.approx(0.0)
    assert tl.kept_spans_src() == []
    assert tl.removed_spans() == [(0.0, 10.0)]
    assert tl.src_to_out(5.0) is None
    assert tl.next_kept_src(0.0) is None


def test_snap_to_frame_grid():
    tl = Timeline.identity(10.0).keep([(0.53, 4.97)])
    snapped = tl.snap(25.0)
    # 0.53*25=13.25→13→0.52 ; 4.97*25=124.25→124→4.96
    assert snapped.kept_spans_src() == [(0.52, 4.96)]
    assert snapped.out_duration == pytest.approx(4.96 - 0.52)


def test_snap_drops_sub_frame_segments():
    tl = Timeline.identity(10.0).keep([(1.0, 3.0), (5.001, 5.01), (7.0, 9.0)])
    snapped = tl.snap(25.0)
    # 5.001→125.025→125=5.0 ; 5.01→125.25→125=5.0 → collapses → dropped
    assert snapped.kept_spans_src() == [(1.0, 3.0), (7.0, 9.0)]


def test_snap_reindexes_output():
    tl = Timeline.identity(10.0).keep([(1.01, 3.02), (6.04, 8.05)])
    snapped = tl.snap(25.0)
    segs = snapped.segments
    assert segs[0].out_start == pytest.approx(0.0)
    assert segs[1].out_start == pytest.approx(segs[0].out_end)


def test_to_dict_round_trip():
    tl = (
        Timeline.identity(10.0)
        .keep([(1.0, 3.0), (6.0, 8.0)])
        .with_speed(1.25)
    )
    d = tl.to_dict()
    assert d["source_duration"] == pytest.approx(10.0)
    assert d["removed"] == [list(map(pytest.approx, s)) for s in
                            [(0.0, 1.0), (3.0, 6.0), (8.0, 10.0)]]
    back = Timeline.from_dict(d)
    assert back.source_duration == tl.source_duration
    assert len(back.segments) == len(tl.segments)
    for a, b in zip(back.segments, tl.segments):
        assert a.src_start == pytest.approx(b.src_start)
        assert a.src_end == pytest.approx(b.src_end)
        assert a.out_start == pytest.approx(b.out_start)
        assert a.speed == pytest.approx(b.speed)
        assert a.out_end == pytest.approx(b.out_end)
    assert back.removed_spans() == tl.removed_spans()


def test_round_trip_random_keep_intervals():
    """Review Focus 5: every kept point round-trips; removed points map to
    None and next_kept_src jumps to the next kept boundary."""
    rng = random.Random(20261115)
    for _ in range(40):
        n = rng.randint(1, 6)
        pts = sorted(rng.uniform(0.0, 60.0) for _ in range(2 * n))
        keep = [(pts[i], pts[i + 1]) for i in range(0, 2 * n, 2)]
        keep = [(a, b) for a, b in keep if b - a > 0.05]
        tl = Timeline.identity(60.0).keep(keep)
        # interior kept points round-trip
        for a, b in tl.kept_spans_src():
            mid = (a + b) / 2.0
            out = tl.src_to_out(mid)
            assert out is not None
            assert tl.out_to_src(out) == pytest.approx(mid, abs=1e-6)
            for frac in (0.01, 0.99):
                t = a + (b - a) * frac
                out = tl.src_to_out(t)
                assert tl.out_to_src(out) == pytest.approx(t, abs=1e-6)
        # removed mid-points: None + next boundary
        for a, b in tl.removed_spans():
            mid = (a + b) / 2.0
            assert tl.src_to_out(mid) is None
            nxt = tl.next_kept_src(mid)
            after = [s for (s, e) in tl.kept_spans_src() if s >= mid]
            if after:
                assert nxt == pytest.approx(min(after))
            else:
                assert nxt is None
