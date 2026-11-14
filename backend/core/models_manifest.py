"""Model manifest (SPEC §8): every ML model and its cache dir under ``models/``.

``configure_model_env()`` must run before importing any ML library so HF /
torch caches land under ``models/`` instead of the user profile.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from backend.core.paths import MODELS_DIR


@dataclass(frozen=True)
class ModelSpec:
    name: str
    engine: str
    source: str            # "huggingface:<repo>", "bundled", or URL description
    filename: str | None   # None = multi-file / managed by the engine itself
    cache_subdir: str      # under MODELS_DIR
    approx_mb: int
    sha256: str | None = None


MODELS: dict[str, ModelSpec] = {
    "deepfilternet3": ModelSpec(
        name="DeepFilterNet3",
        engine="deepfilternet",
        source="github:Rikorose/DeepFilterNet (auto-downloaded by df.enhance)",
        filename=None,
        cache_subdir="deepfilternet",
        approx_mb=120,
    ),
    "htdemucs": ModelSpec(
        name="HTDemucs",
        engine="audio-separator (demucs)",
        source="github:facebookresearch/demucs remote weights",
        filename="955717e8-8726e21a.th",
        cache_subdir="audio-separator",
        approx_mb=90,
    ),
    "kim_vocal_2": ModelSpec(
        name="Kim_Vocal_2",
        engine="audio-separator (MDX/onnx)",
        source="huggingface:seanghay/uvr_models",
        filename="Kim_Vocal_2.onnx",
        cache_subdir="audio-separator",
        approx_mb=60,
    ),
    "silero_vad": ModelSpec(
        name="Silero VAD",
        engine="silero-vad",
        source="bundled",  # weights ship inside the PyPI package
        filename=None,
        cache_subdir="silero-vad",
        approx_mb=2,
    ),
    "whisper_small": ModelSpec(
        name="faster-whisper small",
        engine="faster-whisper (ctranslate2)",
        source="huggingface:Systran/faster-whisper-small",
        filename=None,
        cache_subdir="whisper",
        approx_mb=460,
    ),
}


def model_dir(name: str) -> Path:
    """Cache dir for ``name`` under ``models/`` (created)."""
    spec = MODELS[name]
    d = MODELS_DIR / spec.cache_subdir
    d.mkdir(parents=True, exist_ok=True)
    return d


def configure_model_env() -> None:
    """Point every library cache at ``models/``. Call before ML imports."""
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("HF_HOME", str(MODELS_DIR / "huggingface"))
    os.environ.setdefault("HF_HUB_CACHE", str(MODELS_DIR / "huggingface" / "hub"))
    os.environ.setdefault("TORCH_HOME", str(MODELS_DIR / "torch"))
    os.environ.setdefault("XDG_CACHE_HOME", str(MODELS_DIR / "xdg"))
