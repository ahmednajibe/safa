"""M10b: silero_vad's loader passes a plain str path to torch.jit.load —
C fopen can't express non-ANSI characters, so a package extracted under an
Arabic (or otherwise non-ASCII) path fails with errno 2. Our patch routes
the read through Python's Unicode-aware open() + a file object.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import torch


def _jit_src() -> Path:
    import silero_vad

    return Path(silero_vad.__file__).parent / "data" / "silero_vad.jit"


def test_patched_jit_loader_uses_file_object(monkeypatch):
    import silero_vad.model as model_mod
    import silero_vad.utils_vad as uv

    from backend.media import silero_patch

    silero_patch.patch_silero_unicode()

    seen = {}
    real_load = torch.jit.load

    def spy(f, *a, **k):
        seen["arg"] = f
        return real_load(f, *a, **k)

    monkeypatch.setattr(torch.jit, "load", spy)

    model = uv.init_jit_model(str(_jit_src()))
    assert model is not None
    assert not isinstance(seen["arg"], (str, Path))
    # `silero_vad.model` bound the name at import time — must be the
    # patched function too, not the original str-path loader.
    assert model_mod.init_jit_model is uv.init_jit_model


def test_patched_loader_handles_unicode_path(tmp_path):
    import silero_vad.utils_vad as uv

    from backend.media import silero_patch

    silero_patch.patch_silero_unicode()

    dst = tmp_path / "نماذج صوت" / "silero_vad.jit"
    dst.parent.mkdir(parents=True)
    shutil.copy(_jit_src(), dst)

    # sanity: upstream behavior really would fail on this path
    assert not str(dst).isascii()

    model = uv.init_jit_model(str(dst))
    assert model is not None


def test_patch_is_idempotent():
    import silero_vad.utils_vad as uv

    from backend.media import silero_patch

    silero_patch.patch_silero_unicode()
    fn = uv.init_jit_model
    silero_patch.patch_silero_unicode()
    assert uv.init_jit_model is fn
