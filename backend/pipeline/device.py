"""Per-stage device resolution (SPEC §9).

Only ``separate``, AI ``denoise`` and ``subtitles`` can use the GPU, and only
when the engine's backend is actually usable. ``job_device="cpu"`` is a hard
ceiling — the whole job then runs on the processor.
"""
from __future__ import annotations

from backend.core.capabilities import Capabilities
from backend.pipeline.types import JobDevice


def gpu_usable(caps: Capabilities) -> bool:
    """Same rule as /api/health.device (SPEC §9)."""
    return bool(caps.gpu and caps.cuda_dlls and caps.modules.get("torch"))


def resolve_device(
    job_device: JobDevice, op_id: str, params: dict, caps: Capabilities
) -> JobDevice:
    """Effective device for one stage of a job."""
    if job_device == "cpu" or not gpu_usable(caps):
        return "cpu"
    params = params or {}
    if op_id == "separate":
        model = params.get("model", "htdemucs")
        if model == "kim_vocal_2":
            # MDX models run through ONNX Runtime CUDAExecutionProvider.
            return "gpu" if caps.modules.get("onnxruntime") else "cpu"
        return "gpu"  # htdemucs → torch CUDA
    if op_id == "denoise":
        engine = params.get("engine", "ai")
        if engine == "ai":
            return "gpu" if caps.modules.get("df") else "cpu"
        return "cpu"  # classic afftdn is ffmpeg
    if op_id == "subtitles":
        return "gpu" if caps.modules.get("faster_whisper") else "cpu"
    return "cpu"
