"""declick — click removal (SPEC §2, M4c Task 1, M4d rework).

Modes (registry ``mode`` param):
- ``mouth`` (default): saliva clicks / lip smacks — the M4d STFT ridge
  detector + band-split repair in ``backend.media.mouthclick``, streamed
  in 10 s blocks with 50 ms overlap (two passes: detect, then
  copy+patch). ``sensitivity`` maps to the detector's excess threshold:
  gentle 9 dB, normal 7 dB, strong 5 dB.
- ``crackle``: digital crackle — the ffmpeg ``adeclick`` mapping from M4a.
  ``gentle``: ``w=55:o=75:t=4`` (higher threshold, only obvious clicks);
  ``normal``: ``t=2``; ``strong``: ``t=1.5:b=4`` (lower threshold + stronger
  burst fusion).

``_residual=True`` runs the hidden M4d ``declick_final`` pass — the same
mouth detector at a fixed 8 dB threshold with magnitude-clamp-only
repair (no AR fill), catching what the earlier chain exposed.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

from backend.core.messages import msg
from backend.media.ffmpeg_run import run_filter
from backend.media.mouthclick import detect_mouth_clicks, repair_mouth_clicks
from backend.operations.base import OpContext, OpResult, stage_wav
from backend.pipeline.types import CancelledError

_CRACKLE = {
    "gentle": "adeclick=w=55:o=75:t=4",
    "normal": "adeclick=t=2",
    "strong": "adeclick=t=1.5:b=4",
}
_Z = {"gentle": 9.0, "normal": 7.0, "strong": 5.0}
_RESIDUAL_Z = 8.0
_BLOCK_S = 10.0
_OVERLAP_S = 0.05
_XFADE_S = 0.002   # ≥ 2 ms crossfades — no 2.5 kHz ringing at patch edges
_CTX_S = 0.06      # STFT baseline / AR fit context, each side
_MERGE_GAP_S = 0.001
_MERGE_MAX_S = 0.010


def _merge_spans(
    spans: list[tuple[int, int]], gap: int, max_w: int
) -> list[tuple[int, int]]:
    """Merge spans ≤``gap`` apart (tick bursts) capped at ``max_w``."""
    out: list[list[int]] = []
    for a, b in sorted(spans):
        if out and a - out[-1][1] <= gap and b - out[-1][0] <= max_w:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [tuple(s) for s in out]


def _detect_all(
    fin: sf.SoundFile, sr: int, z: float, ctx: OpContext
) -> list[list[tuple[int, int]]]:
    """Pass 1: click spans per channel. Each 10 s block is scanned with 50 ms
    of context on both sides (the ±15 ms per-bin baseline needs its window);
    a span is owned by the block its start falls in, so nothing is
    double-counted."""
    n, ch = fin.frames, fin.channels
    block, ov = int(sr * _BLOCK_S), int(sr * _OVERLAP_S)
    spans: list[list[tuple[int, int]]] = [[] for _ in range(ch)]
    pos = 0
    while pos < n:
        if ctx.should_cancel():
            raise CancelledError()
        a, b = max(0, pos - ov), min(n, pos + block + ov)
        fin.seek(a)
        seg = fin.read(b - a, dtype="float64", always_2d=True)
        for c in range(ch):
            for s, e in detect_mouth_clicks(seg[:, c], sr, z):
                if pos <= s + a < pos + block:
                    spans[c].append((s + a, e + a))
        pos += block
        ctx.report(0.45 * pos / n, None)
    gap, max_w = int(sr * _MERGE_GAP_S), int(sr * _MERGE_MAX_S)
    return [_merge_spans(s, gap, max_w) for s in spans]


def _patch_block(
    fin: sf.SoundFile,
    blk: np.ndarray,
    blk_start: int,
    chan: int,
    spans: list[tuple[int, int]],
    sr: int,
    mode: str,
) -> None:
    """Write repaired audio for every span intersecting the block.

    The patch's context always comes from the *input*, so the result is
    identical whichever side of a block boundary it is computed from.
    """
    n = fin.frames
    xf = int(sr * _XFADE_S)
    pad = int(sr * _CTX_S)
    for a, b in spans:
        pa, pb = max(0, a - xf), min(n, b + xf)
        if pb <= blk_start or pa >= blk_start + len(blk):
            continue
        ca, cb = max(0, pa - pad), min(n, pb + pad)
        fin.seek(ca)
        seg = fin.read(cb - ca, dtype="float64", always_2d=True)[:, chan]
        local = [
            (max(ca, s) - ca, min(cb, e) - ca)
            for s, e in spans
            if e > ca and s < cb
        ]
        patched = repair_mouth_clicks(seg, sr, local, mode=mode)
        w0 = max(pa, blk_start)
        w1 = min(pb, blk_start + len(blk))
        blk[w0 - blk_start:w1 - blk_start, chan] = patched[w0 - ca:w1 - ca]


def _event_count(
    spans: list[list[tuple[int, int]]], sr: int
) -> int:
    """Union of the per-channel span lists — one physical click usually
    fires on both channels of a stereo file, and counting them
    separately doubles the reported number."""
    flat = sorted(s for ch in spans for s in ch)
    pad = int(0.005 * sr)
    count, last_end = 0, -1 << 60
    for s, e in flat:
        if s - pad > last_end:
            count += 1
        last_end = max(last_end, e)
    return count


def _mouth(
    in_wav: Path, out_wav: Path, z: float, ctx: OpContext, mode: str
) -> int:
    with sf.SoundFile(str(in_wav)) as fin:
        sr = int(fin.samplerate)
        spans = _detect_all(fin, sr, z, ctx)
        n_spans = _event_count(spans, sr)
        block = int(sr * _BLOCK_S)
        with sf.SoundFile(
            str(out_wav), "w", sr, fin.channels,
            subtype="FLOAT", format="WAV",
        ) as fout:
            pos = 0
            while pos < fin.frames:
                if ctx.should_cancel():
                    raise CancelledError()
                fin.seek(pos)
                blk = fin.read(
                    min(block, fin.frames - pos),
                    dtype="float64", always_2d=True,
                )
                for c in range(fin.channels):
                    _patch_block(fin, blk, pos, c, spans[c], sr, mode)
                fout.write(blk.astype(np.float32))
                pos += len(blk)
                ctx.report(0.45 + 0.55 * pos / fin.frames, None)
    return n_spans


def run(
    in_wav: Path,
    params: dict,
    ctx: OpContext,
    *,
    _residual: bool = False,
    _op_id: str = "declick",
) -> OpResult:
    params = params or {}
    out = stage_wav(ctx, _op_id)
    if not _residual and params.get("mode", "mouth") == "crackle":
        afilter = _CRACKLE.get(
            params.get("sensitivity", "normal"), _CRACKLE["normal"]
        )
        run_filter(in_wav, out, afilter, ctx)
        return OpResult(path=out)
    z = (
        _RESIDUAL_Z
        if _residual
        else _Z.get(params.get("sensitivity", "normal"), _Z["normal"])
    )
    mode = "residual" if _residual else "normal"
    try:
        n = _mouth(in_wav, out, z, ctx, mode)
    except BaseException:
        out.unlink(missing_ok=True)
        raise
    text, code, p = msg("clicks_removed", count=n)
    return OpResult(
        path=out, message=text, message_code=code, message_params=p
    )
