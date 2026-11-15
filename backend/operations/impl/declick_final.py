"""declick_final — hidden residual mouth-click pass (M4d Task 1).

Runs at order 52, immediately after ``enhance``: denoise lowers the
noise floor and enhance's presence boost lifts the 2.5–12 kHz band, so
clicks the first pass hid behind now stand out. Same detector at a
fixed 8 dB threshold, but repair is magnitude-clamping only — no AR
fill — so the gentle tail pass can never inject energy.

Auto-inserted by ``resolve()`` whenever ``declick`` is enabled; not
listed in ``/api/operations`` and has no op card.
"""
from __future__ import annotations

from pathlib import Path

from backend.operations.base import OpContext, OpResult
from backend.operations.impl import declick


def run(in_wav: Path, params: dict, ctx: OpContext) -> OpResult:
    return declick.run(
        in_wav,
        params or {},
        ctx,
        _residual=True,
        _op_id="declick_final",
    )
