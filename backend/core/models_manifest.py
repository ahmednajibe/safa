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
    revision: str | None = None      # pinned tag/commit the download uses
    measured_bytes: int = 0          # on-disk size, measured (M10)


MODELS: dict[str, ModelSpec] = {
    "deepfilternet3": ModelSpec(
        name="DeepFilterNet3",
        engine="deepfilternet",
        source="github:Rikorose/DeepFilterNet@v0.5.6 DeepFilterNet3.zip",
        filename=None,
        cache_subdir="deepfilternet",
        approx_mb=9,
        # sha of the release zip; extracted dir ≈ 8.7 MB on disk
        sha256="49c52edc8947ae1f9bf50d81530beaf3a2c3245aeaf34b6f31ff535cd22284d2",
        revision="v0.5.6",
        measured_bytes=8_716_140,
    ),
    "htdemucs": ModelSpec(
        name="HTDemucs",
        engine="audio-separator (demucs)",
        source="github:facebookresearch/demucs remote weights",
        filename="955717e8-8726e21a.th",
        cache_subdir="audio-separator",
        approx_mb=84,
        sha256="8726e21a993978c7ba086d3872e7608d7d5bfca646ca4aca459ffda844faa8b4",
        measured_bytes=84_170_199,
    ),
    "kim_vocal_2": ModelSpec(
        name="Kim_Vocal_2",
        engine="audio-separator (MDX/onnx)",
        source="huggingface:seanghay/uvr_models",
        filename="Kim_Vocal_2.onnx",
        cache_subdir="audio-separator",
        approx_mb=67,
        sha256="ce74ef3b6a6024ce44211a07be9cf8bc6d87728cc852a68ab34eb8e58cde9c8b",
        measured_bytes=66_778_952,
    ),
    "silero_vad": ModelSpec(
        name="Silero VAD",
        engine="silero-vad",
        source="bundled",  # weights ship inside the PyPI package
        filename=None,
        cache_subdir="silero-vad",
        approx_mb=2,
        measured_bytes=2_000_000,
    ),
    "whisper_small": ModelSpec(
        name="faster-whisper small",
        engine="faster-whisper (ctranslate2)",
        source="huggingface:Systran/faster-whisper-small",
        filename=None,
        cache_subdir="whisper",
        approx_mb=486,
        # model.bin at the pinned revision
        sha256="3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671",
        revision="536b0662742c02347bc0e980a01041f333bce120",
        measured_bytes=486_212_372,
    ),
    "whisper_turbo": ModelSpec(
        name="faster-whisper large-v3-turbo",
        engine="faster-whisper (ctranslate2)",
        source="huggingface:mobiuslabsgmbh/faster-whisper-large-v3-turbo",
        filename=None,
        cache_subdir="whisper",
        approx_mb=1622,
        sha256="e76620f83d5f5b69efd3d87e3dc180c1bd21df9fbebacfd4335e5e1efcc018da",
        revision="0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf",
        measured_bytes=1_621_665_983,
    ),
}


def model_dir(name: str) -> Path:
    """Cache dir for ``name`` under ``models/`` (created)."""
    spec = MODELS[name]
    d = MODELS_DIR / spec.cache_subdir
    d.mkdir(parents=True, exist_ok=True)
    return d


def configure_model_env(offline: bool = True) -> None:
    """Point every library cache at ``models/``. Call before ML imports.

    ``offline=True`` (M10 T4): after setup the app must work without
    network — HF/transformers get their offline flags so a missing model
    fails fast (model_missing) instead of hanging on a download attempt.
    The model downloader itself runs ``configure_model_env(False)`` —
    it is the only component allowed online.
    """
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    os.environ["HF_HOME"] = str(MODELS_DIR / "huggingface")
    os.environ["HF_HUB_CACHE"] = str(MODELS_DIR / "huggingface" / "hub")
    os.environ["TORCH_HOME"] = str(MODELS_DIR / "torch")
    os.environ["XDG_CACHE_HOME"] = str(MODELS_DIR / "xdg")
    if offline:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    else:
        os.environ.pop("HF_HUB_OFFLINE", None)
        os.environ.pop("TRANSFORMERS_OFFLINE", None)


def install_socket_guard() -> None:
    """Block every outbound socket except literal loopback (M10 T4 test
    hook). Worker processes install it when ``SOUND_SYSTEM_SOCKET_GUARD=1``
    is in their env — a job that completes under it proves the offline
    guarantee end-to-end (any real-world connect would raise OSError
    mid-run instead of silently phoning home)."""
    import socket

    _orig_connect = socket.socket.connect
    _orig_create = socket.create_connection

    def _ok(host) -> bool:
        return host in ("127.0.0.1", "::1", "localhost")

    def _guarded_connect(sock, address):
        host = address[0] if isinstance(address, tuple) else address
        if not _ok(host):
            raise OSError(f"socket guard: outbound connect to {host!r}")
        return _orig_connect(sock, address)

    def _guarded_create(address, *a, **kw):
        if not _ok(address[0] if isinstance(address, tuple) else address):
            raise OSError(f"socket guard: outbound connect to {address!r}")
        return _orig_create(address, *a, **kw)

    socket.socket.connect = _guarded_connect
    socket.create_connection = _guarded_create
