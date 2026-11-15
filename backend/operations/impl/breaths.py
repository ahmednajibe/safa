"""breaths — "Reduce breaths" (SPEC §2, M6b Task 2).

CPU-only, order 46 (after dereverb 42, before enhance 50). Attenuates
the spans ``detect_breaths`` finds — never deletes them, so room tone
and phrasing stay natural. Gain envelope on the 10 ms detection grid:

- 20 ms raised-cosine attack into ``-reduction`` at the breath start;
- hold across the detected span;
- 35 ms raised-cosine release after the span end — shortened to a
  15 ms release *inside* the span end when a voiced onset follows
  within 60 ms, so initial consonants are never ducked;
- per-span clamp: the reduction never pushes the breath's peak RMS
  below ``floor_dbfs + 2`` (same VAD floor the detector uses) — a
  breath already near the room floor is only dipped slightly.

Long files stream through ``process_ola`` blocks; detection runs on
the mono mixdown (the only full-length array held in RAM, same pattern
as the other analysis ops). Summary ``breaths_reduced{count, db}``.
"""
from __future__ import annotations

import itertools
import shutil
from pathlib import Path

import numpy as np
import soundfile as sf

from backend.core.messages import msg
from backend.media.breaths import (
    FRAME_S, HOP_S, SENSITIVITIES, detect_breaths,
)
from backend.media.chunks import chunk_spans, process_ola
from backend.operations.base import OpContext, OpResult, stage_wav

_ATTACK_S = 0.020
_RELEASE_S = 0.035
_RELEASE_FAST_S = 0.015
_ONSET_NEAR_S = 0.060
_CHUNK_S = 20.0
_OVERLAP_S = 2.0


def _mono_of(wav: Path, sr: int) -> np.ndarray:
    """Mono mixdown streamed in 60 s reads — peak RAM is mono only."""
    parts = []
    with sf.SoundFile(str(wav)) as f:
        while True:
            blk = f.read(int(60 * sr), dtype="float32", always_2d=True)
            if not len(blk):
                break
            parts.append(blk.mean(axis=1))
    if not parts:
        return np.zeros(0, np.float32)
    return np.ascontiguousarray(np.concatenate(parts), dtype=np.float32)


def _gain_env_db(
    spans: list[tuple[float, float]],
    detail: dict,
    n_samples: int,
    reduce_db: float,
    sr: int,
) -> np.ndarray:
    """Per-hop gain envelope in dB (0 = unity), deepest duck wins.

    env[i] applies around sample ``i * hop + frame/2``.
    """
    floor = detail["floor_dbfs"] + 2.0
    rms, onsets = detail["rms_db"], detail["voiced_onsets"]
    n_hop = n_samples // int(HOP_S * sr) + 64
    env = np.zeros(n_hop, dtype=np.float32)
    atk = max(1, round(_ATTACK_S / HOP_S))
    rel = max(1, round(_RELEASE_S / HOP_S))
    rel_fast = max(1, round(_RELEASE_FAST_S / HOP_S))
    rise = 0.5 - 0.5 * np.cos(np.pi * (np.arange(atk) + 0.5) / atk)
    fall = 0.5 + 0.5 * np.cos(np.pi * (np.arange(rel) + 0.5) / rel)
    fall_fast = (
        0.5 + 0.5 * np.cos(np.pi * (np.arange(rel_fast) + 0.5) / rel_fast)
    )
    for s, e in spans:
        ia = int(round(s / HOP_S))
        ib = int(round(e / HOP_S))
        if ib <= ia or ia >= len(env):
            continue
        ib = min(ib, len(env) - 1)
        peak = float(rms[ia: ib + 1].max())
        red = min(reduce_db, peak - floor)
        if red <= 0.05:
            continue  # breath already sits on the floor — leave it
        nxt = next((o for o in onsets if o * HOP_S >= e - 1e-9), None)
        fast = nxt is not None and (nxt * HOP_S - e) <= _ONSET_NEAR_S

        def dip(a0: int, a1: int, shape: np.ndarray) -> None:
            a0, a1 = max(0, a0), min(a1, len(env))
            if a1 > a0:
                env[a0:a1] = np.minimum(env[a0:a1], shape[: a1 - a0])

        dip(ia, ia + atk, -red * rise)
        if fast:
            # release inside the span, ending one hop before ``ib`` —
            # env[i] applies at ~(i+1) hops, so this keeps the 45 ms
            # pre-voicing collar at unity gain.
            r0 = max(ia + atk, ib - 1 - rel_fast)
            dip(ia + atk, r0, np.full(max(0, r0 - ia - atk), -red))
            dip(r0, ib - 1, -red * fall_fast)
        else:
            dip(ia + atk, ib, np.full(max(0, ib - ia - atk), -red))
            dip(ib, ib + rel, -red * fall)
    return env


def run(
    in_wav: Path, params: dict | None, ctx: OpContext
) -> OpResult:
    params = params or {}
    try:
        reduce_db = float(params.get("reduce_db", 12))
    except (TypeError, ValueError):
        reduce_db = 12.0
    reduce_db = min(20.0, max(6.0, reduce_db))
    sensitivity = params.get("sensitivity", "normal")
    if sensitivity not in SENSITIVITIES:
        sensitivity = "normal"
    out = stage_wav(ctx, "breaths")
    sr = ctx.sample_rate
    db = int(round(reduce_db))

    mono = _mono_of(in_wav, sr)
    spans: list[tuple[float, float]] = []
    if len(mono):
        spans, detail = detect_breaths(
            mono, sr, sensitivity=sensitivity, return_detail=True
        )
    ctx.report(0.5, None)
    if ctx.should_cancel():
        from backend.pipeline.types import CancelledError

        raise CancelledError()

    if not len(mono) or not spans:
        shutil.copyfile(in_wav, out)
        ctx.report(1.0, None)
    else:
        env_db = _gain_env_db(spans, detail, len(mono), reduce_db, sr)
        hop = int(HOP_S * sr)
        t_env = np.arange(len(env_db)) * hop + int(FRAME_S * sr) // 2
        chunk, overlap = int(_CHUNK_S * sr), int(_OVERLAP_S * sr)
        block_spans = chunk_spans(len(mono), chunk, overlap)
        it = itertools.count()

        def fn(block: np.ndarray) -> np.ndarray:
            a = block_spans[next(it)][0]
            g_db = np.interp(
                a + np.arange(len(block)), t_env, env_db
            ).astype(np.float32)
            return block * (10.0 ** (g_db / 20.0))[:, None]

        process_ola(
            in_wav, out, chunk, overlap, fn,
            ctx.should_cancel,
            lambda pr: ctx.report(0.5 + 0.5 * pr, None),
        )
    text, code, p = msg("breaths_reduced", count=len(spans), db=db)
    return OpResult(
        path=out,
        actual_device="cpu",
        message=text,
        message_code=code,
        message_params=p,
    )
