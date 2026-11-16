"""CUDA DLL setup, GPU inventory, and VRAM sampling.

Call ``setup_cuda_dlls()`` at worker start *before* importing onnxruntime /
ctranslate2 / torch so the ``site-packages/nvidia/*/bin`` DLLs are visible
(SPEC §8). Every function here must never raise — a missing GPU or missing
nvidia packages is a normal state, not an error.
"""
from __future__ import annotations

import os
import site
import subprocess
import sys
import threading
from pathlib import Path


def _site_packages_dirs() -> list[Path]:
    dirs: list[Path] = []
    candidates: list[str] = []
    try:
        candidates += list(site.getsitepackages())
    except Exception:
        pass
    try:
        candidates.append(site.getusersitepackages())
    except Exception:
        pass
    candidates.append(str(Path(sys.prefix) / "Lib" / "site-packages"))
    for c in candidates:
        p = Path(c)
        if p.is_dir() and p not in dirs:
            dirs.append(p)
    return dirs


def cuda_dll_dirs(is_nt: bool | None = None) -> list[Path]:
    """Existing ``<site-packages>/nvidia/*/{bin,lib}`` dirs — the nvidia-*
    wheels put DLLs in ``bin`` on Windows and ``.so``s in ``lib`` on POSIX.
    Read-only; never raises."""
    if is_nt is None:
        is_nt = os.name == "nt"
    sub = "bin" if is_nt else "lib"
    dirs: list[Path] = []
    try:
        for sp in _site_packages_dirs():
            nvidia = sp / "nvidia"
            if not nvidia.is_dir():
                continue
            for child in sorted(nvidia.iterdir()):
                if (child / sub).is_dir():
                    dirs.append(child / sub)
    except Exception:
        pass
    return dirs


def setup_cuda_dlls(is_nt: bool | None = None) -> list[Path]:
    """Add every ``<site-packages>/nvidia/*/{bin,lib}`` dir to the loader
    search path: ``os.add_dll_directory`` + ``PATH`` on Windows,
    ``LD_LIBRARY_PATH`` on POSIX (``PATH`` plays no role in ``.so``
    resolution there). Returns the dirs added; ``[]`` if none. Never raises.
    """
    if is_nt is None:
        is_nt = os.name == "nt"
    pathvar = "PATH" if is_nt else "LD_LIBRARY_PATH"
    try:
        added = cuda_dll_dirs(is_nt=is_nt)
        if is_nt:
            for lib_dir in added:
                try:
                    if hasattr(os, "add_dll_directory"):
                        os.add_dll_directory(str(lib_dir))
                except OSError:
                    pass
        if added:
            existing = os.environ.get(pathvar, "")
            os.environ[pathvar] = os.pathsep.join(
                [str(d) for d in added] + ([existing] if existing else [])
            )
    except Exception:
        return []
    return added


def gpu_info() -> dict | None:
    """GPU inventory via nvidia-smi; ``None`` if unavailable."""
    try:
        out = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.used,driver_version",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if out.returncode != 0 or not out.stdout.strip():
            return None
        name, total, used, driver = [p.strip() for p in out.stdout.strip().splitlines()[0].split(",")]
        return {
            "name": name,
            "total_mb": int(float(total)),
            "used_mb": int(float(used)),
            "driver": driver,
        }
    except Exception:
        return None


class VramSampler:
    """Context manager: polls ``gpu_info()["used_mb"]`` in a thread.

    After exit, ``peak_mb`` holds peak-used-minus-baseline (0 if no GPU).
    """

    def __init__(self, interval: float = 0.2):
        self.interval = interval
        self.peak_mb: int = 0
        self._baseline: int = 0
        self._peak: int = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _poll(self) -> None:
        while not self._stop.is_set():
            info = gpu_info()
            if info is not None:
                self._peak = max(self._peak, info["used_mb"])
            self._stop.wait(self.interval)

    def __enter__(self) -> "VramSampler":
        info = gpu_info()
        self._baseline = info["used_mb"] if info is not None else 0
        self._peak = self._baseline
        self._stop.clear()
        self._thread = threading.Thread(target=self._poll, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc) -> bool:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        info = gpu_info()
        if info is not None:
            self._peak = max(self._peak, info["used_mb"])
        self.peak_mb = max(0, self._peak - self._baseline)
        return False
