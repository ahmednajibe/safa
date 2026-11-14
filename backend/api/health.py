"""GET /api/health — environment + capability report (SPEC §5/§9)."""
from fastapi import APIRouter

from backend.core.capabilities import detect_capabilities
from backend.pipeline.device import gpu_usable

router = APIRouter()


@router.get("/api/health")
def health() -> dict:
    caps = detect_capabilities()
    return {
        "status": "ok",
        "python": caps.python,
        "gpu": caps.gpu,
        "device": "gpu" if gpu_usable(caps) else "cpu",
        "cuda_dlls": caps.cuda_dlls,
        "ffmpeg": caps.ffmpeg,
        "modules": caps.modules,
        "models": caps.models,
    }
