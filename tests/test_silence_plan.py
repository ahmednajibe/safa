"""M5 Task 3 + M6b Task 3: the pure keep-interval planner (SPEC §10).

``plan_keep`` is pure math: raw speech intervals + gap policy → the
keep list ``render_keep`` consumes. The ``min_silence_s`` check compares
the RAW gap (before padding — M6b fix); pads are then added only where
the gap is actually cut, and in remove mode a detected breath inside a
pad zone clamps the pad at the breath edge. No audio I/O here.
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
    """shorten keeps keep_pause_s of *audible* silence per gap — the
    pads already inside the blocks count toward it (R1 B5)."""
    keep = _plan([(1.0, 2.0), (3.0, 4.0)], 6.0, action="shorten",
                 keep_pause_s=0.2)
    # 2·pad (0.2) already covers keep_pause_s 0.2 → the padded blocks
    # keep their edges; edges keep max(pad, keep_pause) of silence.
    flat = [v for iv in keep for v in iv]
    assert flat == pytest.approx([0.8, 2.1, 2.9, 4.2])


def test_shorten_keeps_exact_pause_small_gap():
    """0.35 s raw gap, keep_pause 0.25, pad 0.1 → exactly 0.25 s of
    silence survives (R1 B5: total kept silence =
    ``min(raw_gap, max(2·pad, keep_pause))``)."""
    keep = _plan([(1.0, 2.0), (2.35, 3.0)], 5.0, action="shorten",
                 keep_pause_s=0.25, pad_s=0.1)
    # kept silence = (block end − speech end) + (speech start − block
    # start) = 0.25 — the removed middle is 0.35 − 0.25 = 0.10.
    removed = keep[1][0] - keep[0][1]
    assert removed == pytest.approx(0.35 - 0.25)
    kept_pause = (keep[0][1] - 2.0) + (2.35 - keep[1][0])
    assert kept_pause == pytest.approx(0.25)


def test_shorten_keeps_exact_pause_3s_gap():
    """A 3.0 s gap keeps the same 0.25 s — the pause budget is per gap,
    not proportional to gap length."""
    keep = _plan([(1.0, 2.0), (5.0, 6.0)], 7.0, action="shorten",
                 keep_pause_s=0.25, pad_s=0.1)
    kept_pause = (keep[0][1] - 2.0) + (5.0 - keep[1][0])
    assert kept_pause == pytest.approx(0.25)


def test_shorten_gap_shorter_than_budget_kept_whole():
    """raw_gap < max(2·pad, keep_pause) → the whole gap survives."""
    keep = _plan([(1.0, 2.0), (2.2, 3.0)], 5.0, action="shorten",
                 keep_pause_s=0.25, pad_s=0.1, min_silence_s=0.1)
    # raw gap 0.2 < 0.25 → nothing removed between the blocks; the
    # file edges keep their own max(pad, keep_pause) = 0.25 budget.
    assert keep == [(0.75, 3.25)]


def test_shorten_keep_pause_zero_behaves_like_remove():
    """keep_pause_s=0 still can't un-keep the pads — kept silence per
    gap is exactly 2·pad."""
    keep = _plan([(1.0, 2.0), (3.0, 4.0)], 5.0, action="shorten",
                 keep_pause_s=0.0, pad_s=0.1)
    assert keep == [(0.9, 2.1), (2.9, 4.1)]


def test_gap_exactly_min_silence_is_cut():
    """gap >= min_silence_s is a real silence (cut/shorten applies)."""
    keep = _plan([(1.0, 1.5), (2.0, 2.5)], 5.0, pad_s=0.0,
                 min_silence_s=0.5)
    assert keep == [(1.0, 1.5), (2.0, 2.5)]


def test_gap_just_below_min_silence_kept():
    keep = _plan([(1.0, 1.5), (1.9, 2.5)], 5.0, pad_s=0.0,
                 min_silence_s=0.5)
    assert keep == [(1.0, 2.5)]


def test_raw_gap_compared_before_padding():
    """M6b Review Focus 5: raw VAD gap 0.4 s with min_silence_s 0.3 IS
    cut — the old code padded first (0.4 − 2·0.1 = 0.2 < 0.3) and kept it.
    """
    keep = _plan([(1.0, 2.0), (2.4, 3.0)], 5.0)
    assert keep == [(0.9, 2.1), (2.3, 3.1)]


def test_raw_gap_boundary_uses_unpadded_gap():
    """0.29 s raw gap < 0.3 → kept whole even though padding would
    shrink it to 0.09 s on the blocks."""
    keep = _plan([(1.0, 2.0), (2.29, 3.0)], 5.0)
    assert keep == [(0.9, 3.1)]


# ---- breath-aware pad clamp (M6b) --------------------------------------------------


def test_breath_in_trailing_pad_clamps_pad_at_breath_start():
    """Exhale right after a phrase end inside the pad zone: the kept pad
    must stop at the breath start so the splice keeps no gasp."""
    speech = [(1.0, 2.0), (2.5, 3.5)]
    # raw gap 0.5 ≥ 0.3 → cut; pad zone after 2.0 is (2.0, 2.1)
    keep = _plan(speech, 5.0, breaths=[(2.04, 2.30)])
    assert keep == [(0.9, 2.04), (2.4, 3.6)]


def test_breath_in_leading_pad_clamps_pad_at_breath_end():
    """Inhale ending inside the next phrase's leading pad zone: the pad
    starts at the breath end instead of keeping the gasp tail."""
    speech = [(1.0, 2.0), (2.5, 3.5)]
    keep = _plan(speech, 5.0, breaths=[(2.30, 2.46)])
    assert keep == [(0.9, 2.1), (2.46, 3.6)]


def test_breath_covering_whole_leading_pad_drops_pad():
    """Breath spanning the entire pad zone → no pad at all."""
    speech = [(1.0, 2.0), (2.5, 3.5)]
    keep = _plan(speech, 5.0, breaths=[(2.30, 2.52)])
    # be 2.52 > a 2.5 → straddles speech start: not clamped (speech wins)
    assert keep == [(0.9, 2.1), (2.4, 3.6)]
    keep = _plan(speech, 5.0, breaths=[(2.38, 2.50)])
    assert keep == [(0.9, 2.1), (2.5, 3.6)]


def test_breath_outside_pad_zone_ignored():
    speech = [(1.0, 2.0), (3.0, 4.0)]
    keep = _plan(speech, 5.0, breaths=[(2.3, 2.5)])
    assert keep == [(0.9, 2.1), (2.9, 4.1)]


def test_breaths_ignored_in_shorten_mode():
    """Shorten keeps a piece of every pause — breath clamping is a
    remove-mode rule only."""
    speech = [(1.0, 2.0), (2.5, 3.5)]
    keep = _plan(speech, 5.0, action="shorten", keep_pause_s=0.2,
                 breaths=[(2.04, 2.30)])
    # R1 B5: 2·pad already covers keep_pause 0.2 → padded blocks
    # (0.9,2.1),(2.4,3.6) keep their edges; each file edge keeps
    # max(pad, keep_pause) = 0.2 → leading (0.8,·), trailing (·,3.7)
    flat = [v for iv in keep for v in iv]
    assert flat == pytest.approx([0.8, 2.1, 2.4, 3.7])


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
    # R1 B5: edge silence kept = max(pad, keep_pause) = 0.25 total —
    # leading: block starts at 2.0−0.25; trailing: ends at 3.0+0.25
    assert keep == [(1.75, 3.25)]


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
