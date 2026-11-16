"""silero_vad's bundled-model loader can't open non-ANSI paths (M10b).

``load_silero_vad`` resolves ``silero_vad/data/silero_vad.jit`` and calls
``torch.jit.load(str_path)`` — torch hands the path to C ``fopen``, which
can only express the ANSI codepage, so an app folder containing e.g.
Arabic characters dies with ``errno 2`` inside the denoise/silence
stages. ``torch.jit.load`` also accepts a file-like object, so the patch
below opens through Python's Unicode-aware ``open()`` instead.

Only the jit loader needs this: the ONNX wrapper hands its path to
onnxruntime, which converts to wide chars on Windows and already loads
Unicode paths correctly.
"""
from __future__ import annotations

import sys


def _init_jit_model_unicode(model_path, device=None):
    import torch

    if device is None:
        device = torch.device("cpu")
    with open(model_path, "rb") as fh:
        model = torch.jit.load(fh, map_location=device)
    model.eval()
    return model


def patch_silero_unicode() -> None:
    """Rebind silero_vad's jit loader to the file-object path. Idempotent;
    call after ``import silero_vad`` and before ``load_silero_vad()`` —
    both ``utils_vad`` and ``model`` hold their own binding of
    ``init_jit_model`` (``from ... import``)."""
    import silero_vad.utils_vad as uv

    if getattr(uv, "_ss_unicode_patched", False):
        return
    uv.init_jit_model = _init_jit_model_unicode
    uv._ss_unicode_patched = True
    model_mod = sys.modules.get("silero_vad.model")
    if model_mod is not None:
        model_mod.init_jit_model = _init_jit_model_unicode
