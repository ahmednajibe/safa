"""Integrated loudness (EBU R128) via ffmpeg's ebur128 filter.

FEASIBILITY gotcha: per-frame ``I:`` lines precede the Summary — parse the
**last** ``I:`` value, which is the integrated measurement.
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import soundfile as sf

from backend.core.ffmpeg import find_ffmpeg
from backend.core.proc import ProcError, run

_I_RE = re.compile(r"I:\s+(-?\d+(?:\.\d+)?|-inf)\s+LUFS")
_TP_RE = re.compile(r"Peak:\s+(-?\d+(?:\.\d+)?|-inf)\s+dBFS")


def integrated_lufs(wav: Path) -> float | None:
    """Integrated LUFS of ``wav``; ``None`` for silence (<= -70 / -inf)."""
    cp = run(
        [
            find_ffmpeg(), "-hide_banner",
            "-i", wav, "-af", "ebur128", "-f", "null", "-",
        ],
    )
    matches = _I_RE.findall(cp.stderr or "")
    if not matches:
        return None
    last = matches[-1]
    if last == "-inf":
        return None
    value = float(last)
    return None if value <= -70.0 else value


def level_stats(wav: Path) -> dict:
    """Input level stats for the enhance op's dynamic thresholds (M4c).

    20 ms frame RMS of the mono mix, streamed in ~1 s reads (bounded RAM):
    ``floor_dbfs`` = 20th percentile, ``speech_dbfs`` = 85th percentile.
    """
    frame_ms = 20
    rms_chunks: list[np.ndarray] = []
    with sf.SoundFile(str(wav)) as f:
        sr = int(f.samplerate)
        frame = max(1, int(sr * frame_ms / 1000))
        while True:
            data = f.read(sr, dtype="float32", always_2d=True)
            if len(data) == 0:
                break
            mono = data.mean(axis=1)
            n = len(mono) // frame * frame
            if n:
                fr = mono[:n].reshape(-1, frame)
                rms_chunks.append(np.sqrt((fr * fr).mean(axis=1)))
    rms = np.concatenate(rms_chunks) if rms_chunks else np.zeros(1)
    db = 20.0 * np.log10(np.maximum(rms, 1e-9))
    return {
        "floor_dbfs": float(np.percentile(db, 20)),
        "speech_dbfs": float(np.percentile(db, 85)),
    }


def true_peak_db(wav: Path) -> float:
    """True-peak of ``wav`` in dBFS via ``ebur128=peak=true`` (Summary block;
    ``-inf`` for digital silence)."""
    cp = run(
        [
            find_ffmpeg(), "-hide_banner",
            "-i", wav, "-af", "ebur128=peak=true", "-f", "null", "-",
        ],
    )
    matches = _TP_RE.findall(cp.stderr or "")
    if not matches:
        raise RuntimeError(f"no true-peak reading for {wav}")
    return float(matches[-1])
