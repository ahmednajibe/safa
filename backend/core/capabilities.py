"""Cheap capability detection for /api/health and op availability.

Hard rule (M2 plan): this module must never import torch / onnxruntime /
ctranslate2 / df / audio_separator — the API process stays light so startup
is < 3 s. Presence is probed with ``importlib.util.find_spec`` and by looking
at files under ``models/``.
"""
from __future__ import annotations

import importlib.util
import platform
from dataclasses import dataclass
from functools import cache

from backend.core.ffmpeg import (
    ffmpeg_version,
    find_ffmpeg,
    find_ffprobe,
    has_encoder,
)
from backend.core.gpu import cuda_dll_dirs, gpu_info
from backend.core.models_manifest import MODELS, model_dir

MODULES = (
    "torch",
    "df",
    "audio_separator",
    "silero_vad",
    "pedalboard",
    "faster_whisper",
    "onnxruntime",
)


@dataclass
class Capabilities:
    python: str
    gpu: dict | None
    cuda_dlls: bool
    ffmpeg: dict
    modules: dict[str, bool]
    models: dict[str, bool]


def _module_present(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _ffmpeg_info() -> dict:
    try:
        return {
            "path": str(find_ffmpeg()),
            "ffprobe_path": str(find_ffprobe()),
            "version": ffmpeg_version(),
            "nvenc": has_encoder("h264_nvenc"),
        }
    except Exception as exc:  # ffmpeg missing is a state, not a crash
        return {"error": str(exc)}


def _model_present(name: str) -> bool:
    """Model files present under ``model_dir(name)`` (per M2 plan checks)."""
    try:
        d = model_dir(name)
        if name == "htdemucs":
            # weights + demucs yaml + the download manifest the library
            # reads before every load_model (offline safety, M7).
            spec = MODELS[name]
            return (
                (d / spec.filename).exists()
                and (d / "htdemucs.yaml").exists()
                and (d / "download_checks.json").exists()
            )
        if name == "kim_vocal_2":
            # weights + all three metadata JSONs the MDX path downloads.
            return (
                (d / MODELS[name].filename).exists()
                and (d / "download_checks.json").exists()
                and (d / "vr_model_data.json").exists()
                and (d / "mdx_model_data.json").exists()
            )
        if name == "deepfilternet3":
            p = d / "DeepFilterNet3"
            return p.is_dir() and any(p.iterdir())
        if name in ("whisper_small", "whisper_turbo"):
            # HF hub layout under models/whisper/ — check the *specific*
            # repo folder so one model's cache can't fake the other (M8).
            folder = {
                "whisper_small": "models--Systran--faster-whisper-small",
                "whisper_turbo": (
                    "models--mobiuslabsgmbh--"
                    "faster-whisper-large-v3-turbo"
                ),
            }[name]
            return any((d / folder).glob("snapshots/*/model.bin"))
        if name == "silero_vad":
            return _module_present("silero_vad")  # weights bundled in the wheel
    except Exception:
        return False
    return False


def _detect() -> Capabilities:
    return Capabilities(
        python=platform.python_version(),
        gpu=gpu_info(),
        cuda_dlls=bool(cuda_dll_dirs()),
        ffmpeg=_ffmpeg_info(),
        modules={name: _module_present(name) for name in MODULES},
        models={name: _model_present(name) for name in MODELS},
    )


@cache
def detect_capabilities() -> Capabilities:
    return _detect()


def refresh_capabilities() -> Capabilities:
    detect_capabilities.cache_clear()
    return detect_capabilities()


__all__ = [
    "Capabilities",
    "MODULES",
    "detect_capabilities",
    "refresh_capabilities",
]
