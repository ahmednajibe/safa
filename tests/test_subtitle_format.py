"""M8 Task 2: cue builder + SRT/VTT writers (argon_m8 Q3).

Rules under test: ≤42 visible chars per line, ≤2 lines per cue, cue
duration 1–7 s, ≥80 ms between cues, no word ever split, lines containing
Arabic ([؀-ۿ]) start with U+200F (not counted in the 42), `،؛؟` attach to
the preceding word, SRT is utf-8-sig (BOM), VTT is utf-8 (no BOM).
"""
import random
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.media.subtitle_format import (
    RLM,
    build_cues,
    visible_len,
    write_srt,
    write_vtt,
)

AR_RE = re.compile(r"[؀-ۿ]")

AR_WORDS = [
    "مرحبا", "العالم", "كيف", "حالك", "اختبار", "صوت", "تسجيل",
    "عربي", "كلام", "اليوم", "شكرا", "جزيلا", "الصوت", "ملف",
]
EN_WORDS = [
    "the", "quick", "brown", "fox", "jumps", "over", "lazy", "dog",
    "testing", "sound", "system", "number", "one", "two", "three",
]
PUNCT = [",", ".", "؟", "،", "؛", "!", "?", ":", "»", "«"]


def W(start, end, text):
    return SimpleNamespace(start=start, end=end, text=text)


def SEG(words):
    return SimpleNamespace(words=words)


def _random_stream(rng: random.Random) -> list:
    """Utterances of 4–18 words, gaps 0.7–1.6 s; within an utterance word
    durs 0.15–0.45 s and gaps 0–0.15 s. Punctuation tokens (whisper-style
    orphan tokens) follow ~25% of words."""
    segments = []
    t = 0.5
    for _ in range(rng.randint(4, 10)):
        words = []
        for _ in range(rng.randint(4, 18)):
            dur = rng.uniform(0.15, 0.45)
            pool = AR_WORDS if rng.random() < 0.5 else EN_WORDS
            words.append(W(t, t + dur, rng.choice(pool)))
            t += dur
            if rng.random() < 0.25:
                t += 0.01
                words.append(W(t, t + 0.01, rng.choice(PUNCT)))
                t += 0.01
            t += rng.uniform(0.0, 0.15)
        segments.append(SEG(words))
        t += rng.uniform(0.7, 1.6)
    return segments, t


def _assert_cue_rules(cues, duration):
    prev_end = None
    for c in cues:
        assert 1 <= len(c.lines) <= 2, c.lines
        for line in c.lines:
            assert visible_len(line) <= 42, line
            if AR_RE.search(line):
                assert line.startswith(RLM), line
            else:
                assert not line.startswith(RLM), line
            # punctuation never orphaned at a line start or alone
            stripped = line.lstrip(RLM).strip()
            assert stripped and stripped[0] not in ",.;?!:؟،؛»"
        assert c.end - c.start <= 7.0 + 1e-6
        assert c.end - c.start >= 1.0 - 1e-6
        assert 0 <= c.start < c.end <= duration + 1e-6
        if prev_end is not None:
            assert c.start - prev_end >= 0.08 - 1e-6
        prev_end = c.end


# ---- writers -------------------------------------------------------------------


def test_srt_format_and_bom(tmp_path):
    cues = build_cues(
        [SEG([W(0.5, 1.0, "Hello"), W(1.0, 1.4, "world.")])], 30.0
    )
    p = tmp_path / "a.srt"
    write_srt(cues, p)
    raw = p.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")  # utf-8-sig BOM
    text = raw.decode("utf-8-sig")
    assert "00:00:00,500 --> 00:00:01" in text
    assert "Hello world." in text
    assert text.strip().splitlines()[0] == "1"


def test_vtt_format_no_bom(tmp_path):
    cues = build_cues(
        [SEG([W(0.5, 1.0, "Hello"), W(1.0, 1.4, "world.")])], 30.0
    )
    p = tmp_path / "a.vtt"
    write_vtt(cues, p)
    raw = p.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8")
    assert text.startswith("WEBVTT\n")
    assert "00:00:00.500 --> 00:00:01" in text


def test_empty_cues_valid_files(tmp_path):
    srt, vtt = tmp_path / "e.srt", tmp_path / "e.vtt"
    write_srt([], srt)
    write_vtt([], vtt)
    srt_raw = srt.read_bytes()
    assert srt_raw.startswith(b"\xef\xbb\xbf")
    assert len(srt_raw.decode("utf-8-sig").strip()) == 0
    assert vtt.read_text(encoding="utf-8").startswith("WEBVTT")


# ---- cue builder --------------------------------------------------------------


def test_arabic_line_gets_rlm(tmp_path):
    cues = build_cues(
        [SEG([W(0.0, 0.4, "مرحبا"), W(0.4, 0.8, "بالعالم؟")])], 30.0
    )
    p = tmp_path / "a.srt"
    write_srt(cues, p)
    lines = [
        l for l in p.read_text(encoding="utf-8-sig").splitlines()
        if "مرحبا" in l
    ]
    assert lines and lines[0].startswith(RLM)


def test_rlm_not_counted_in_42():
    # 42-char Arabic line stays legal after the RLM prefix is added
    line = "ء" * 42
    assert visible_len(RLM + line) == 42


def test_stray_punctuation_attaches_to_previous_word():
    cues = build_cues(
        [SEG([
            W(0.0, 0.3, "كيف"),
            W(0.31, 0.32, "؟"),   # orphan token as whisper emits it
            W(0.4, 0.8, "حالك"),
        ])],
        30.0,
    )
    text = " ".join(l.lstrip(RLM) for c in cues for l in c.lines)
    assert "كيف؟" in text
    assert not any(l.lstrip(RLM).strip() == "؟" for c in cues
                   for l in c.lines)


def test_never_split_a_word():
    long_word = "pneumonoultramicroscopicsilicovolcanoconiosis" * 2  # >42
    cues = build_cues(
        [SEG([W(0.0, 0.9, long_word), W(1.0, 1.3, "ok.")])], 30.0
    )
    joined = " ".join(l.lstrip(RLM) for c in cues for l in c.lines)
    assert long_word in joined  # intact


def test_min_gap_80ms_and_duration_bounds():
    rng = random.Random(20261115)
    for _ in range(30):
        segments, end = _random_stream(rng)
        cues = build_cues(segments, end + 1.0)
        _assert_cue_rules(cues, end + 1.0)


def test_cues_within_output_duration():
    rng = random.Random(7)
    segments, end = _random_stream(rng)
    cues = build_cues(segments, end + 1.0)
    for c in cues:
        assert 0 <= c.start < c.end <= end + 1.0 + 1e-6


def test_single_word_cue_extends_to_one_second():
    cues = build_cues([SEG([W(0.2, 0.5, "مرحبا")])], 30.0)
    assert cues[0].end - cues[0].start == pytest.approx(1.0)


def test_dense_stream_still_respects_gap():
    """Worst case: back-to-back words with no pause — cues must still be
    separated by ≥80 ms and stay monotonic."""
    words = []
    t = 0.0
    rng = random.Random(3)
    for _ in range(80):
        dur = rng.uniform(0.15, 0.4)
        words.append(W(t, t + dur, rng.choice(EN_WORDS)))
        t += dur + rng.uniform(0.0, 0.05)
    cues = build_cues([SEG(words)], t + 1.0)
    _assert_cue_rules(cues, t + 1.0)


def test_two_line_cap_forces_new_cue():
    words = [W(i * 0.3, i * 0.3 + 0.25, f"w{i:02d}") for i in range(30)]
    cues = build_cues([SEG(words)], 60.0)
    assert len(cues) > 1
    for c in cues:
        assert len(c.lines) <= 2
