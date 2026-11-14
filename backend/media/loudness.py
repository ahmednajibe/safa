"""Integrated loudness (EBU R128) via ffmpeg's ebur128 filter.

FEASIBILITY gotcha: per-frame ``I:`` lines precede the Summary — parse the
**last** ``I:`` value, which is the integrated measurement.
"""
from __future__ import annotations

import re
from pathlib import Path

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
