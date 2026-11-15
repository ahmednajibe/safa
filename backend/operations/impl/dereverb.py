"""dereverb — "Reduce room echo" (SPEC §2, M6 Task 2).

CPU-only, order 42 (after denoise 40, before enhance 50). Two tools from
the user-approved experiment (try_wpe.py variant F):

- ``wpe``  : off | on (10 taps) | strong (24 taps) — ≤ 20 s blocks, 2 s
  raised-cosine crossfades.
- cuts   : ``find_room_resonances`` on the (possibly dereverbed) audio,
  notched by ``strength``: gentle 3 / normal 4.5 / strong 6 dB cap.

Summary is always ``room_resonances_cut{freqs: [...]}`` — an empty list
honestly says "no resonances found" (and the output then equals the
input when WPE is off).
"""
from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import soundfile as sf

from backend.core.messages import msg
from backend.media.chunks import process_ola
from backend.media.room import apply_cuts, find_room_resonances, wpe
from backend.operations.base import OpContext, OpResult, stage_wav

_STRENGTH_CUT_DB = {"gentle": 3.0, "normal": 4.5, "strong": 6.0}
_WPE_TAPS = {"off": 0, "on": 10, "strong": 24}
_WPE_CHUNK_S = 20.0
_WPE_OVERLAP_S = 2.0


def run(
    in_wav: Path, params: dict | None, ctx: OpContext
) -> OpResult:
    params = params or {}
    max_cut = _STRENGTH_CUT_DB.get(params.get("strength", "normal"), 4.5)
    taps = _WPE_TAPS.get(params.get("wpe", "on"), 10)
    out = stage_wav(ctx, "dereverb")
    sr = ctx.sample_rate

    work = in_wav
    if taps:
        wpe_wav = ctx.work_dir / "stage_42_dereverb_wpe.wav"

        def fn(block: np.ndarray) -> np.ndarray:
            res = np.empty_like(block)
            for c in range(block.shape[1]):
                res[:, c] = wpe(block[:, c], taps=taps)
            return res

        process_ola(
            in_wav,
            wpe_wav,
            int(_WPE_CHUNK_S * sr),
            int(_WPE_OVERLAP_S * sr),
            fn,
            ctx.should_cancel,
            lambda p: ctx.report(0.6 * p, None),
        )
        work = wpe_wav

    cuts = find_room_resonances(
        work,
        should_cancel=ctx.should_cancel,
        report=lambda p: ctx.report(0.6 + 0.3 * p, None),
    )
    if cuts:
        x, _ = sf.read(str(work), dtype="float32", always_2d=True)
        y = apply_cuts(x, sr, cuts, max_cut)
        sf.write(str(out), y, sr, subtype="FLOAT")
    elif work != in_wav:
        shutil.copyfile(work, out)
    else:
        # no resonances, no WPE — output equals input
        shutil.copyfile(in_wav, out)
    ctx.report(1.0, None)
    freqs = [int(round(f)) for f, _ in cuts]
    text, code, p = msg("room_resonances_cut", freqs=freqs)
    return OpResult(
        path=out,
        actual_device="cpu",
        message=text,
        message_code=code,
        message_params=p,
    )
