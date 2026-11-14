"""speed — pitch-preserving tempo change via ffmpeg ``atempo`` (SPEC §2).

``atempo`` preserves pitch but its output length can drift a few ms from
``n_in / factor``; the op forces the exact frame count afterwards (pad with
digital silence or drop the tail — never more than a few ms) so the audio
length matches the timeline's ``out_duration`` and the video re-render.
ffmpeg's WAV carries INFO string chunks so libsndfile can't open it ``r+``
— length fixing is a stream copy instead of an in-place edit.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

from backend.media.ffmpeg_run import run_filter
from backend.media.timeline import Timeline
from backend.operations.base import OpContext, OpResult, stage_wav

_BLOCK = 1 << 16


def _atempo_chain(factor: float) -> str:
    """One or more atempo stages; a single filter only spans 0.5–100."""
    parts: list[str] = []
    f = factor
    while f > 100.0:
        parts.append("atempo=100.0")
        f /= 100.0
    while f < 0.5:
        parts.append("atempo=0.5")
        f /= 0.5
    parts.append(f"atempo={f:.6f}")
    return ",".join(parts)


def _exact_length(src: Path, dst: Path, target: int) -> None:
    """Copy ``src`` → ``dst`` at exactly ``target`` frames (pad/truncate)."""
    info = sf.info(str(src))
    with (
        sf.SoundFile(str(src), "r") as fin,
        sf.SoundFile(
            str(dst), "w", samplerate=info.samplerate,
            channels=info.channels, format="WAV", subtype="FLOAT",
        ) as fout,
    ):
        copied = 0
        while copied < target:
            data = fin.read(
                min(_BLOCK, target - copied), dtype="float32",
                always_2d=True,
            )
            if len(data) == 0:
                break
            fout.write(data)
            copied += len(data)
        pad = target - copied
        zeros = np.zeros(
            (min(_BLOCK, max(pad, 1)), info.channels), dtype="float32"
        )
        while pad > 0:
            n = min(len(zeros), pad)
            fout.write(zeros[:n])
            pad -= n


def run(in_wav: Path, params: dict, ctx: OpContext) -> OpResult:
    factor = float((params or {}).get("factor", 1.0))
    if factor <= 0:
        raise ValueError("Speed must be greater than zero")
    parent = ctx.timeline or Timeline.identity(ctx.duration_s)
    if abs(factor - 1.0) < 1e-9:
        return OpResult(path=in_wav, timeline=parent)

    out = stage_wav(ctx, "speed")
    raw = ctx.work_dir / "stage_70_speed_raw.wav"
    run_filter(in_wav, raw, _atempo_chain(factor), ctx)
    n_in = sf.info(str(in_wav)).frames
    _exact_length(raw, out, round(n_in / factor))
    raw.unlink(missing_ok=True)
    return OpResult(path=out, timeline=parent.with_speed(factor))
