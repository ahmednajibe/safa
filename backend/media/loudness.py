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
