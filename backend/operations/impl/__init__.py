"""Op implementations (M4a). ``IMPLEMENTATIONS[op_id]`` is what the runner
dispatches to; ids absent here raise ``NotImplementedError`` as before.

Each module registers itself below. Imports are guarded so a broken optional
dependency (e.g. pedalboard) only takes its own ops down — ffmpeg ops keep
working and the failure stays an honest ``NotImplementedError``.
"""
from __future__ import annotations

from backend.operations.base import OpFn

IMPLEMENTATIONS: dict[str, OpFn] = {}


def _register(op_id: str, module: str) -> None:
    try:
        mod = __import__(module, fromlist=["run"])
    except Exception:
        return
    IMPLEMENTATIONS[op_id] = mod.run


for _op_id in (
    "channel", "highpass", "dehum", "separate", "declick", "denoise",
    "dereverb", "breaths", "enhance", "loudness", "trim", "speed",
    "silence",
):
    _register(_op_id, f"{__name__}.{_op_id}")

del _op_id, _register
