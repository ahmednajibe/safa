"""DeepFilterNet3 engine adapter (M6 Task 1, SPEC §2/§8, FEASIBILITY #3).

One model per worker process and device, chunked overlap-add through
``process_ola`` (≤ 30 s blocks, 1 s raised-cosine crossfades) so a 60-min
file stays inside the RAM/VRAM budget; a GPU ``OutOfMemoryError`` mid-file
frees the engine and retries the whole file on CPU. Workarounds from
docs/FEASIBILITY.md:

- ``df.enhance.get_cache_dir`` is patched to ``model_dir("deepfilternet3")``
  before ``init_df()`` — never pass ``model_base_dir``.
- The torchaudio ``AudioMetaData`` shim only if the import fails.
- ``enhance()`` resolves ``get_device()`` from ``torch.cuda.is_available``
  internally → CPU runs need it patched False for init *and* inference.
- DFN works at 48 kHz only — ``process_file`` asserts the input rate.

Heavy imports (torch, df) stay lazy inside functions so an ffmpeg-only job
never pays the model import cost.
"""
from __future__ import annotations

import contextlib
import gc
import sys
import types
import unittest.mock as mock
from pathlib import Path
from typing import Literal

import numpy as np
import soundfile as sf

from backend.core.models_manifest import model_dir
from backend.media.chunks import process_ola

Device = Literal["gpu", "cpu"]

SR = 48000
_CHUNK_S = 30.0
_OVERLAP_S = 1.0


def _return_none_on_oserror(fn):
    """Wrap a probe so a missing `git` binary behaves like not-a-repo."""

    def inner(*a, **k):
        try:
            return fn(*a, **k)
        except OSError:
            return None

    return inner


def patch_df_portability() -> None:
    """deepfilternet 0.5.6 spawns ``git`` inside ``get_git_root()`` and
    ``get_branch_name()`` (reached via ``init_df -> init_logger``) and only
    catches ``CalledProcessError`` — on a machine without git (every
    portable install) that escapes as ``FileNotFoundError`` and kills the
    denoise stage. Wrap the probes to return ``None`` like the not-a-repo
    path, and rebind every already-imported ``df.*`` module that holds its
    own reference (``from df.utils import ...``). Idempotent; called lazily
    from ``DfnEngine.load`` before ``init_df``."""
    import df.utils

    if getattr(df.utils, "_ss_git_probe_patched", False):
        return
    names = ("get_git_root", "get_branch_name", "get_commit_hash")
    originals = {
        name: getattr(df.utils, name)
        for name in names
        if getattr(df.utils, name, None) is not None
    }
    for name, orig in originals.items():
        setattr(df.utils, name, _return_none_on_oserror(orig))
    df.utils._ss_git_probe_patched = True
    for mod in list(sys.modules.values()):
        if not str(getattr(mod, "__name__", "")).startswith("df."):
            continue
        for name, orig in originals.items():
            if getattr(mod, name, None) is orig:
                setattr(mod, name, getattr(df.utils, name))


class DfnEngine:
    """One loaded DeepFilterNet3 model on one device."""

    def __init__(self) -> None:
        self._model = None
        self._df_state = None
        self.device: Device | None = None

    def load(self, device: Device) -> "DfnEngine":
        import torch  # noqa: F401 — loaded lazily

        try:
            import df.enhance  # noqa: F401
        except Exception:
            # deepfilternet 0.5.6 imports
            # torchaudio.backend.common.AudioMetaData; shim only needed if
            # the import actually fails (SPEC §8).
            import torchaudio

            common = types.ModuleType("torchaudio.backend.common")
            common.AudioMetaData = torchaudio.AudioMetaData
            sys.modules["torchaudio.backend.common"] = common

        mod = sys.modules["df.enhance"]
        patch_df_portability()
        if not getattr(mod, "_ss_cache_dir_patched", False):
            # init_df() -> maybe_download_model uses get_cache_dir (appdirs
            # user cache); redirect to models/deepfilternet (FEASIBILITY #3).
            mod.get_cache_dir = lambda: str(model_dir("deepfilternet3"))
            mod._ss_cache_dir_patched = True

        ctx = (
            mock.patch("torch.cuda.is_available", lambda: False)
            if device == "cpu"
            else contextlib.nullcontext()
        )
        with ctx:
            # enhance() resolves get_device() internally — keep the CPU
            # patch active for init (and again at inference in process()).
            model, df_state, _ = mod.init_df(log_level="ERROR")
            model = model.to("cuda" if device == "gpu" else "cpu").eval()
        self._model, self._df_state, self.device = model, df_state, device
        return self

    def process(self, x: np.ndarray, atten_lim_db: float) -> np.ndarray:
        """Denoise one mono float32 block (same length in/out)."""
        import torch
        from df.enhance import enhance

        ctx = (
            mock.patch("torch.cuda.is_available", lambda: False)
            if self.device == "cpu"
            else contextlib.nullcontext()
        )
        block = np.ascontiguousarray(x, dtype=np.float32)
        with ctx, torch.no_grad():
            # enhance() calls audio.numpy() then moves features to the
            # model's device itself — the input tensor stays on CPU.
            out = enhance(
                self._model,
                self._df_state,
                torch.from_numpy(block)[None, :],
                atten_lim_db=atten_lim_db,
            )
        y = out.squeeze().cpu().numpy().astype(np.float32)
        if len(y) >= len(block):
            return y[: len(block)]
        return np.pad(y, (0, len(block) - len(y)))

    def close(self) -> None:
        """Drop the model and free VRAM."""
        self._model = None
        self._df_state = None
        gc.collect()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass


_ENGINES: dict[Device, DfnEngine] = {}


def get_engine(device: Device) -> DfnEngine:
    """Process-wide engine cache — the model loads once per worker."""
    eng = _ENGINES.get(device)
    if eng is None:
        eng = DfnEngine().load(device)
        _ENGINES[device] = eng
    return eng


def _drop_engine(device: Device) -> None:
    eng = _ENGINES.pop(device, None)
    if eng is not None:
        eng.close()


def close_engines() -> None:
    """Free every cached engine (called at op end — never per chunk)."""
    for dev in list(_ENGINES):
        _drop_engine(dev)


def _is_oom(exc: BaseException) -> bool:
    try:
        import torch

        if isinstance(exc, torch.cuda.OutOfMemoryError):
            return True
    except Exception:
        pass
    return "out of memory" in str(exc).lower()


def _process_all(
    in_wav: Path,
    out_wav: Path,
    atten_lim_db: float,
    device: Device,
    report,
    should_cancel,
) -> None:
    engine = get_engine(device)
    chunk = int(_CHUNK_S * SR)
    overlap = int(_OVERLAP_S * SR)

    def fn(block: np.ndarray) -> np.ndarray:
        out = np.empty_like(block)
        for c in range(block.shape[1]):
            out[:, c] = engine.process(block[:, c], atten_lim_db)
        return out

    process_ola(in_wav, out_wav, chunk, overlap, fn, should_cancel, report)


def process_file(
    in_wav: Path,
    out_wav: Path,
    atten_lim_db: float,
    device: Device,
    report=None,
    should_cancel=None,
) -> Device:
    """Run DeepFilterNet3 over ``in_wav`` (48 kHz only) → ``out_wav``.

    Chunked ≤ 30 s + 1 s crossfade, one channel at a time (stereo stays
    stereo). Returns the device actually used — ``"cpu"`` when a GPU
    ``OutOfMemoryError`` forced the documented SPEC §9 retry (the whole
    file is redone on CPU so every chunk sees the same model state).
    """
    if sf.info(str(in_wav)).samplerate != SR:
        raise AssertionError(
            f"denoise needs 48 kHz input, got"
            f" {sf.info(str(in_wav)).samplerate}"
        )
    actual: Device = "gpu" if device == "gpu" else "cpu"
    try:
        try:
            _process_all(
                in_wav, out_wav, atten_lim_db, actual, report, should_cancel
            )
        except BaseException as exc:
            if actual == "cpu" or not _is_oom(exc):
                raise
            _drop_engine("gpu")  # free VRAM before the CPU retry
            _process_all(
                in_wav, out_wav, atten_lim_db, "cpu", report, should_cancel
            )
            actual = "cpu"
    finally:
        close_engines()  # VRAM freed at op end, never between chunks
    return actual
