"""Silence detection front-ends for the silence op (SPEC §10, M5 Task 2).

Two interchangeable detectors return *speech* intervals in seconds:

- ``speech_intervals_vad`` — Silero VAD on a 16 kHz mono copy. Long files
  are processed in bounded windows (≤ 10 min, 2 s overlap) so RAM stays
  flat; overlapping detections are merged into one sorted interval list.
  Always CPU (the model is tiny and bundled in the wheel).
- ``speech_intervals_amplitude`` — ffmpeg ``silencedetect`` against a
  volume threshold; returns the complement of the detected silences.
  Works on speech-free content (music, karaoke output) where a VAD would
  see nothing.

Both honour ``report(pct 0..1)`` and ``should_cancel()``.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

import numpy as np
import soundfile as sf

from backend.media.ffmpeg_run import run_ffmpeg
from backend.pipeline.types import CancelledError

ReportFn = Callable[[float], None] | None
CancelFn = Callable[[], bool] | None

_VAD_SR = 16000
_WINDOW_S = 600.0    # ≤ 10 min per window (M5 global constraints)
_OVERLAP_S = 2.0
_BLOCK = 1 << 16


def _cancel(should_cancel: CancelFn) -> None:
    if should_cancel and should_cancel():
        raise CancelledError()


def _merge(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    spans = sorted((float(a), float(b)) for a, b in intervals if b > a)
    out: list[list[float]] = []
    for a, b in spans:
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def _resample_16k_mono(data: np.ndarray, sr: int) -> np.ndarray:
    """Mix to mono and resample to 16 kHz (soxr) for the VAD."""
    if data.ndim > 1:
        data = data.mean(axis=1)
    if sr == _VAD_SR:
        return np.ascontiguousarray(data, dtype=np.float32)
    import soxr

    return soxr.resample(data, sr, _VAD_SR, quality="VHQ")


def speech_intervals_vad(
    wav: Path,
    threshold: float = 0.5,
    min_speech_ms: int = 150,
    report: ReportFn = None,
    should_cancel: CancelFn = None,
    window_s: float = _WINDOW_S,
    overlap_s: float = _OVERLAP_S,
) -> list[tuple[float, float]]:
    """Speech intervals (seconds) via Silero VAD on 16 kHz mono windows.

    Each ``window_s`` chunk (with ``overlap_s`` of leading overlap to
    avoid boundary splits) is resampled and classified separately;
    timestamps are shifted into file time and merged. Lazy torch/silero
    imports keep the API process light — only the worker loads the model.
    """
    _cancel(should_cancel)
    emit = report or (lambda p: None)
    info = sf.info(str(wav))
    sr, n_in = info.samplerate, info.frames
    duration = n_in / sr
    if n_in == 0:
        return []

    import torch  # noqa: E402 — worker-side only
    from silero_vad import get_speech_timestamps, load_silero_vad  # noqa: E402

    from backend.media.silero_patch import patch_silero_unicode

    patch_silero_unicode()
    model = load_silero_vad()

    step = max(1.0, window_s - overlap_s)
    # Window starts march by ``step``; a window covering the tail is
    # shortened to EOF (never a trailing empty window).
    starts = []
    t = 0.0
    while t < duration:
        starts.append(t)
        t += step

    raw: list[tuple[float, float]] = []
    with sf.SoundFile(str(wav), "r") as fin:
        for i, start in enumerate(starts):
            _cancel(should_cancel)
            end = min(duration, start + window_s)
            fin.seek(int(round(start * sr)))
            data = fin.read(
                int(round((end - start) * sr)),
                dtype="float32", always_2d=True,
            )
            mono16 = _resample_16k_mono(data, sr)
            _cancel(should_cancel)
            stamps = get_speech_timestamps(
                torch.from_numpy(mono16),
                model,
                threshold=threshold,
                sampling_rate=_VAD_SR,
                min_speech_duration_ms=min_speech_ms,
                min_silence_duration_ms=100,
                return_seconds=True,
            )
            for s in stamps:
                a = start + float(s["start"])
                b = start + float(s["end"])
                raw.append((min(a, duration), min(b, duration)))
            emit((i + 1) / len(starts))
    emit(1.0)
    return _merge(raw)


_SIL_START_RE = re.compile(r"silence_start:\s*([-\d.eE]+)")
_SIL_END_RE = re.compile(r"silence_end:\s*([-\d.eE]+)")


def speech_intervals_amplitude(
    wav: Path,
    threshold_db: float,
    min_silence_s: float,
    report: ReportFn = None,
    should_cancel: CancelFn = None,
) -> list[tuple[float, float]]:
    """Speech intervals = complement of ffmpeg ``silencedetect`` spans.

    ``silencedetect=n=<threshold_db>dB:d=<min_silence_s>`` writes
    ``silence_start``/``silence_end`` lines to stderr; a file that ends in
    silence gets no end line and is closed at the file's duration.
    """
    _cancel(should_cancel)
    emit = report or (lambda p: None)
    info = sf.info(str(wav))
    duration = info.frames / info.samplerate
    if duration <= 0:
        return []

    stderr = run_ffmpeg(
        [
            "-i", wav,
            "-af", f"silencedetect=n={threshold_db:g}dB:d={min_silence_s:g}",
            "-f", "null", "-",
        ],
        duration_s=duration,
        on_pct=lambda p: emit(p / 100.0),
        should_cancel=should_cancel or (lambda: False),
    )

    silences: list[tuple[float, float]] = []
    pending: float | None = None
    for line in stderr.splitlines():
        m = _SIL_START_RE.search(line)
        if m:
            pending = float(m.group(1))
            continue
        m = _SIL_END_RE.search(line)
        if m and pending is not None:
            silences.append((pending, float(m.group(1))))
            pending = None
    if pending is not None:
        silences.append((pending, duration))  # trailing silence

    speech = []
    pos = 0.0
    for a, b in silences:
        if a - pos > 1e-3:
            speech.append((pos, min(a, duration)))
        pos = max(pos, b)
    if duration - pos > 1e-3:
        speech.append((pos, duration))
    emit(1.0)
    return _merge(speech)


def non_speech_dbfs(
    wav: Path,
    intervals: list[tuple[float, float]] | None = None,
    report: ReportFn = None,
    should_cancel: CancelFn = None,
) -> float:
    """RMS level (dBFS) of the audio *outside* detected speech — the fan /
    room-noise floor a denoiser should lower
    (``.orchestrator/analysis/nonspeech.py``).

    Silero-VAD speech intervals on a 16 kHz mono copy; the mask is applied
    to the 48 kHz mono mix streamed in blocks (bounded RAM). ``nan`` when
    nothing is non-speech — there is no floor to measure.
    """
    if intervals is None:
        intervals = speech_intervals_vad(
            wav,
            report=lambda p: (report or (lambda q: None))(0.9 * p),
            should_cancel=should_cancel,
        )
    info = sf.info(str(wav))
    sr, n = info.samplerate, info.frames
    bounds = [(int(a * sr), int(b * sr)) for a, b in intervals]
    sumsq = 0.0
    count = 0
    idx = 0  # interval pointer — bounds are sorted
    with sf.SoundFile(str(wav), "r") as fin:
        pos = 0
        while pos < n:
            _cancel(should_cancel)
            block = fin.read(
                min(_BLOCK, n - pos), dtype="float32", always_2d=True
            )
            if len(block) == 0:
                break
            mask = np.ones(len(block), dtype=bool)
            while idx < len(bounds) and bounds[idx][1] <= pos:
                idx += 1
            j = idx
            while j < len(bounds) and bounds[j][0] < pos + len(block):
                a = max(bounds[j][0] - pos, 0)
                b = min(bounds[j][1] - pos, len(block))
                if b > a:
                    mask[a:b] = False
                j += 1
            mono = block.mean(axis=1)
            ns = mono[mask]
            sumsq += float((ns * ns).sum())
            count += int(mask.sum())
            pos += len(block)
    if report:
        report(1.0)
    if count == 0:
        return float("nan")
    return float(10.0 * np.log10(sumsq / count + 1e-20))
