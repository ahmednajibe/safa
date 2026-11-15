"""M5 Task 3: the pure keep-interval planner (SPEC §10).

``plan_keep`` is pure math: padded speech intervals + gap policy → the
keep list ``render_keep`` consumes. No audio I/O here.
"""
import pytest


def _plan(speech, duration, **kw):
    from backend.media.silence_plan import plan_keep

    defaults = dict(
        action="remove", min_silence_s=0.3, pad_s=0.1, keep_pause_s=0.25
    )
    defaults.update(kw)
    return plan_keep(speech, duration, **defaults)


def _clean(intervals, duration):
    assert all(0.0 <= a < b <= duration for a, b in intervals)
    for (_, b1), (a2, _) in zip(intervals, intervals[1:]):
        assert b1 <= a2


# ---- Review Focus 1: gap policy ------------------------------------------------


def test_short_gap_is_kept():
    """0.2 s between two words < min_silence_s 0.3 → never cut."""
    keep = _plan([(1.0, 2.0), (2.2, 3.0)], 5.0)
    # one merged block: pad reaches across the 0.2 s gap
    assert keep == [(0.9, 3.1)]


def test_one_second_gap_removed():
    keep = _plan([(1.0, 2.0), (3.0, 4.0)], 5.0)
    assert keep == [(0.9, 2.1), (2.9, 4.1)]


def test_one_second_gap_shortened():
    """shorten keeps keep_pause_s of the gap, split half/half — and of
    the leading/trailing silence."""
    keep = _plan([(1.0, 2.0), (3.0, 4.0)], 6.0, action="shorten",
                 keep_pause_s=0.2)
    # gap between padded blocks: 2.1–2.9 (0.8 s) → keep 0.1 each side;
    # leading 0.9 s → keep last 0.2; trailing 1.9 s → keep first 0.2
    assert keep == [(0.7, 2.2), (2.8, 4.3)]


def test_gap_exactly_min_silence_is_cut():
    """gap >= min_silence_s is a real silence (cut/shorten applies)."""
    keep = _plan([(1.0, 1.5), (2.0, 2.5)], 5.0, pad_s=0.0,
                 min_silence_s=0.5)
    assert keep == [(1.0, 1.5), (2.0, 2.5)]


def test_gap_just_below_min_silence_kept():
    keep = _plan([(1.0, 1.5), (1.9, 2.5)], 5.0, pad_s=0.0,
                 min_silence_s=0.5)
    assert keep == [(1.0, 2.5)]


# ---- padding --------------------------------------------------------------------


def test_padding_clamps_at_zero_and_duration():
    keep = _plan([(0.05, 0.5), (4.5, 4.95)], 5.0, pad_s=0.2)
    assert keep == [(0.0, 0.7), (4.3, 5.0)]


def test_padded_blocks_merge():
    # pads overlap → single block
    keep = _plan([(1.0, 1.2), (1.35, 1.6)], 5.0, pad_s=0.1)
    assert len(keep) == 1
    assert keep[0][0] == pytest.approx(0.9)
    assert keep[0][1] == pytest.approx(1.7)


# ---- leading / trailing -----------------------------------------------------------


def test_remove_drops_leading_and_trailing():
    keep = _plan([(2.0, 3.0)], 6.0, pad_s=0.1)
    assert keep == [(1.9, 3.1)]


def test_shorten_keeps_at_most_keep_pause_at_ends():
    keep = _plan([(2.0, 3.0)], 6.0, action="shorten", pad_s=0.1,
                 keep_pause_s=0.25)
    # leading: 1.9 s of silence → keep last 0.25 → block starts 1.65
    # trailing: 2.9 s → keep first 0.25 → block ends 3.35
    assert keep == [(1.65, 3.35)]


def test_shorten_short_edges_kept_whole():
    """Edges shorter than keep_pause_s are preserved entirely — here the
    whole file survives."""
    keep = _plan([(0.2, 4.9)], 5.0, action="shorten", pad_s=0.0,
                 keep_pause_s=0.25)
    assert keep == [(0.0, 5.0)]


def test_remove_with_speech_at_edges():
    keep = _plan([(0.0, 2.0), (3.0, 5.0)], 5.0, pad_s=0.1)
    assert keep == [(0.0, 2.1), (2.9, 5.0)]


# ---- empty / degenerate ---------------------------------------------------------


def test_empty_speech_keeps_everything():
    """No speech → the op turns this into no_speech_found; planner just
    returns the whole file (never an empty render)."""
    assert _plan([], 5.0) == [(0.0, 5.0)]
    assert _plan([], 5.0, action="shorten") == [(0.0, 5.0)]


def test_full_speech_is_identity():
    assert _plan([(0.0, 5.0)], 5.0) == [(0.0, 5.0)]


def test_result_sorted_non_overlapping_in_bounds():
    speech = [(0.5, 1.0), (2.0, 2.5), (3.0, 3.2), (4.0, 4.5)]
    for action in ("remove", "shorten"):
        keep = _plan(speech, 5.0, action=action)
        _clean(keep, 5.0)


def test_unsorted_input_is_sorted():
    keep = _plan([(3.0, 4.0), (1.0, 2.0)], 5.0)
    assert keep == [(0.9, 2.1), (2.9, 4.1)]


def test_total_removed_duration():
    """seconds removed = duration − Σ keep — the op's summary math."""
    keep = _plan([(1.0, 2.0), (3.0, 4.0)], 5.0)
    kept = sum(b - a for a, b in keep)
    assert kept == pytest.approx(2.4)
