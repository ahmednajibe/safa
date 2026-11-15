"""M5 Task 2: silence detection — Silero VAD windows + ffmpeg silencedetect.

``speech_silences.wav`` is SAPI speech with two known gaps
(3.0 s at 5.22, 5.0 s at 13.44 — ``fixtures.json``); VAD must find them
within ±0.3 s. Amplitude mode is checked on a synthesized tone-burst file
(the gaps are digital silence, so ``silencedetect`` is exact).
"""
import json
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

GENERATED = Path(__file__).parent / "fixtures" / "generated"
SPEECH_SIL = GENERATED / "speech_silences.wav"
SILENCE = GENERATED / "silence.wav"
SPEECH = GENERATED / "speech.wav"


def _fixture(name: str) -> dict:
    data = json.loads(
        (GENERATED / "fixtures.json").read_text(encoding="utf-8")
    )
    return next(f for f in data["files"] if f["file"] == name)


def _duration(path: Path) -> float:
    info = sf.info(str(path))
    return info.frames / info.samplerate


def _complement(
    intervals: list[tuple[float, float]], duration: float
) -> list[tuple[float, float]]:
    """Gaps inside ``[0, duration]`` not covered by ``intervals``."""
    out = []
    pos = 0.0
    for a, b in intervals:
        if a - pos > 0.05:
            out.append((pos, a))
        pos = max(pos, b)
    if duration - pos > 0.05:
        out.append((pos, duration))
    return out


def _assert_sorted_clean(intervals, duration):
    assert all(0.0 <= a < b <= duration + 1e-6 for a, b in intervals)
    for (a1, b1), (a2, b2) in zip(intervals, intervals[1:]):
        assert b1 <= a2 + 1e-6, intervals


# ---- VAD ---------------------------------------------------------------------


def test_vad_finds_fixture_gaps():
    """Every recorded silence is matched by a detected gap within ±0.3 s.
    (SAPI speech also has sub-second sentence pauses — VAD correctly
    reports those too, so extra small gaps are allowed.)"""
    from backend.media.silence_detect import speech_intervals_vad

    dur = _duration(SPEECH_SIL)
    speech = speech_intervals_vad(SPEECH_SIL)
    _assert_sorted_clean(speech, dur)
    gaps = _complement(speech, dur)
    expected = [
        tuple(p) for p in _fixture("speech_silences.wav")["silence_intervals"]
    ]
    for ea, eb in expected:
        match = min(
            gaps,
            key=lambda g: abs(g[0] - ea) + abs(g[1] - eb),
            default=None,
        )
        assert match is not None
        assert abs(match[0] - ea) <= 0.3, (match, (ea, eb))
        assert abs(match[1] - eb) <= 0.3, (match, (ea, eb))


def test_vad_all_speech_file_is_covered():
    from backend.media.silence_detect import speech_intervals_vad

    dur = _duration(SPEECH)
    speech = speech_intervals_vad(SPEECH)
    _assert_sorted_clean(speech, dur)
    # SAPI speech fills the file except a sentence pause or two —
    # detection must span start to end and cover almost all of it.
    assert speech[0][0] <= 0.3
    assert speech[-1][1] >= dur - 0.3
    assert sum(b - a for a, b in speech) >= 0.8 * dur


def test_vad_silence_only_returns_empty():
    from backend.media.silence_detect import speech_intervals_vad

    assert speech_intervals_vad(SILENCE) == []


def test_vad_window_merge(tmp_path):
    """A file longer than one window must merge overlaps into the same
    intervals as a single pass (small window forced for the test)."""
    from backend.media.silence_detect import speech_intervals_vad

    dur = _duration(SPEECH_SIL)
    whole = speech_intervals_vad(SPEECH_SIL)
    windowed = speech_intervals_vad(
        SPEECH_SIL, window_s=6.0, overlap_s=2.0
    )
    _assert_sorted_clean(windowed, dur)
    # boundaries of the merged result track the single-pass ones
    assert len(windowed) == len(whole)
    for (a, b), (wa, wb) in zip(whole, windowed):
        assert abs(a - wa) <= 0.3, (whole, windowed)
        assert abs(b - wb) <= 0.3, (whole, windowed)


def test_vad_reports_progress_and_cancels():
    from backend.media.silence_detect import speech_intervals_vad
    from backend.pipeline.types import CancelledError

    seen = []
    speech_intervals_vad(
        SPEECH_SIL, window_s=6.0, overlap_s=2.0, report=seen.append
    )
    assert seen and seen[-1] == pytest.approx(1.0)
    assert all(0.0 <= p <= 1.0 for p in seen)

    with pytest.raises(CancelledError):
        speech_intervals_vad(SPEECH_SIL, should_cancel=lambda: True)


# ---- amplitude ----------------------------------------------------------------


def _tone_bursts(path: Path) -> list[tuple[float, float]]:
    """1 s tone | 2 s silence | 1 s tone | 0.5 s silence | 1 s tone."""
    sr = 48000
    t = np.arange(sr, dtype=np.float32) / sr
    tone = 0.5 * np.sin(2 * np.pi * 440 * t)
    zero2 = np.zeros(2 * sr, np.float32)
    zhalf = np.zeros(sr // 2, np.float32)
    parts = [(tone, True), (zero2, False), (tone, True),
             (zhalf, False), (tone, True)]
    sf.write(str(path), np.concatenate([p[0] for p in parts]), sr)
    speech, pos = [], 0.0
    for data, is_tone in parts:
        end = pos + len(data) / sr
        if is_tone:
            speech.append((pos, end))
        pos = end
    return speech


def test_amplitude_finds_tone_gaps(tmp_path):
    from backend.media.silence_detect import speech_intervals_amplitude

    wav = tmp_path / "tones.wav"
    expected = _tone_bursts(wav)
    dur = _duration(wav)
    speech = speech_intervals_amplitude(wav, threshold_db=-40,
                                        min_silence_s=0.4)
    _assert_sorted_clean(speech, dur)
    # the 2 s gap is detected; the 0.5 s gap is ≥ min_silence too
    gaps = _complement(speech, dur)
    assert len(gaps) == 2
    assert gaps[0][0] == pytest.approx(1.0, abs=0.1)
    assert gaps[0][1] == pytest.approx(3.0, abs=0.1)
    assert gaps[1][0] == pytest.approx(4.0, abs=0.1)
    assert gaps[1][1] == pytest.approx(4.5, abs=0.1)
    # speech covers the tone spans
    for (ea, eb) in expected:
        assert any(
            a <= ea + 0.1 and b >= eb - 0.1 for a, b in speech
        ), (ea, eb, speech)


def test_amplitude_trailing_silence(tmp_path):
    """A file ending in silence: silencedetect never prints silence_end —
    the trailing silence must close at the file's duration."""
    from backend.media.silence_detect import speech_intervals_amplitude

    sr = 48000
    t = np.arange(sr, dtype=np.float32) / sr
    sig = np.concatenate(
        [0.5 * np.sin(2 * np.pi * 440 * t), np.zeros(2 * sr, np.float32)]
    )
    wav = tmp_path / "trailing.wav"
    sf.write(str(wav), sig, sr)
    speech = speech_intervals_amplitude(wav, threshold_db=-40,
                                        min_silence_s=0.4)
    _assert_sorted_clean(speech, 3.0)
    assert len(speech) == 1
    assert speech[0][0] <= 0.1
    assert speech[0][1] == pytest.approx(1.0, abs=0.1)


def test_amplitude_silence_only_returns_empty():
    from backend.media.silence_detect import speech_intervals_amplitude

    assert speech_intervals_amplitude(
        SILENCE, threshold_db=-40, min_silence_s=0.3
    ) == []


def test_amplitude_cancels():
    from backend.media.silence_detect import speech_intervals_amplitude
    from backend.pipeline.types import CancelledError

    with pytest.raises(CancelledError):
        speech_intervals_amplitude(
            SPEECH_SIL, threshold_db=-40, min_silence_s=0.3,
            should_cancel=lambda: True,
        )
