"""Bounded-RAM chunked processing for file↔file ops (M6).

``chunk_spans`` lays out ≤ ``chunk``-sample blocks covering the input with
≥ ``overlap`` samples shared between neighbours (a short tail is merged
into a final full-length block so every block has full context).
``process_ola`` streams the file through a per-block ``fn`` and stitches
the outputs with raised-cosine crossfades — output length equals input
length exactly, and peak RAM is one block plus the model's own.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import numpy as np
import soundfile as sf

from backend.pipeline.types import CancelledError

BlockFn = Callable[[np.ndarray], np.ndarray]
ReportFn = Callable[[float], None] | None
CancelFn = Callable[[], bool] | None


def chunk_spans(n: int, chunk: int, overlap: int) -> list[tuple[int, int]]:
    """``[start, end)`` spans covering ``[0, n)``; each ≤ ``chunk`` long,
    consecutive spans share ≥ ``overlap`` samples.

    Starts march by ``chunk - overlap``; a tail shorter than ``chunk`` is
    replaced by a final full-length block ending at ``n`` (more context
    for the model, and never a degenerate tiny tail block).
    """
    if n <= 0:
        return []
    if chunk <= 0 or overlap < 0 or overlap >= chunk:
        raise ValueError(f"need 0 <= overlap < chunk, got {overlap}/{chunk}")
    if n <= chunk:
        return [(0, n)]
    hop = chunk - overlap
    starts = [0]
    a = hop
    while a + chunk < n:
        starts.append(a)
        a += hop
    last = n - chunk
    if starts[-1] != last:
        starts.append(last)
    return [(s, min(s + chunk, n)) for s in starts]


def process_ola(
    in_wav: Path,
    out_wav: Path,
    chunk: int,
    overlap: int,
    fn: BlockFn,
    should_cancel: CancelFn = None,
    report: ReportFn = None,
) -> None:
    """Process ``in_wav`` through ``fn`` in ≤ ``chunk``-sample blocks with
    ``overlap``-sample raised-cosine crossfades, writing ``out_wav``.

    ``fn`` gets a float32 ``(frames, channels)`` block and must return the
    same shape/length (short tails get the same treatment). The crossfade
    blends the previous block's held-back tail into each block's head, so
    boundaries stay continuous even though each block is processed
    independently. Output frames == input frames, always.
    """
    emit = report or (lambda p: None)
    info = sf.info(str(in_wav))
    sr, n, ch = info.samplerate, info.frames, info.channels
    spans = chunk_spans(n, chunk, overlap)
    if not spans:
        raise ValueError(f"empty input: {in_wav}")
    try:
        with sf.SoundFile(str(in_wav)) as fin, sf.SoundFile(
            str(out_wav), "w", sr, ch, subtype="FLOAT", format="WAV"
        ) as fout:
            pending: np.ndarray | None = None  # held-back tail to blend
            for i, (a, b) in enumerate(spans):
                if should_cancel and should_cancel():
                    raise CancelledError()
                fin.seek(a)
                block = fin.read(b - a, dtype="float32", always_2d=True)
                y = np.asarray(fn(block), dtype=np.float32)
                if y.ndim == 1:
                    y = y[:, None]
                if len(y) > len(block):
                    y = y[: len(block)]
                elif len(y) < len(block):
                    y = np.pad(y, ((0, len(block) - len(y)), (0, 0)))
                if pending is not None:
                    ov = len(pending)
                    w = (
                        0.5
                        - 0.5
                        * np.cos(np.pi * (np.arange(ov) + 0.5) / ov)
                    ).astype(np.float32)[:, None]
                    y[:ov] = pending * (1.0 - w) + y[:ov] * w
                nxt = spans[i + 1][0] if i + 1 < len(spans) else b
                keep = max(0, b - nxt)  # tail held back for the next blend
                write_len = len(y) - keep
                fout.write(y[:write_len])
                pending = y[write_len:] if keep else None
                emit((i + 1) / len(spans))
    except BaseException:
        Path(out_wav).unlink(missing_ok=True)
        raise
    emit(1.0)
