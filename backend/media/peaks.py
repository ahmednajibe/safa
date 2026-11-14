"""Waveform peaks computed by streaming blocks (never loads whole files).

Output shape per channel: ``data[ch] = [[min, max], ...]`` — one pair per
``1/per_second`` of audio, values rounded to 4 decimals in -1..1.
"""
from __future__ import annotations

import numpy as np
import soundfile as sf

from pathlib import Path


def compute_peaks(
    wav: Path, per_second: int = 50, max_points: int = 200_000
) -> dict:
    """Min/max peak pairs per channel; ``per_second`` lowered to fit budget."""
    info = sf.info(wav)
    sr = info.samplerate
    frames = info.frames
    channels = info.channels
    duration = frames / sr if sr else 0.0

    eff_per_second = per_second
    if duration > 0 and duration * eff_per_second > max_points:
        eff_per_second = max(1, int(max_points / duration))
    block = max(1, round(sr / eff_per_second))

    mins = np.full(channels, np.inf, dtype=np.float64)
    maxs = np.full(channels, -np.inf, dtype=np.float64)
    data: list[list[list[float]]] = [[] for _ in range(channels)]

    def flush() -> None:
        for ch in range(channels):
            mn = mins[ch] if np.isfinite(mins[ch]) else 0.0
            mx = maxs[ch] if np.isfinite(maxs[ch]) else 0.0
            data[ch].append([round(float(mn), 4), round(float(mx), 4)])

    remainder = 0
    for chunk in sf.blocks(wav, blocksize=block, always_2d=True):
        mins = np.minimum(mins, chunk.min(axis=0))
        maxs = np.maximum(maxs, chunk.max(axis=0))
        remainder = len(chunk)
        if remainder == block:
            flush()
            mins = np.full(channels, np.inf)
            maxs = np.full(channels, -np.inf)
            remainder = 0
    if remainder:
        flush()

    return {
        "sample_rate": sr,
        "channels": channels,
        "duration_s": round(duration, 4),
        "per_second": eff_per_second,
        "data": data,
    }
