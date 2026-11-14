"""Subprocess wrapper (M3a plan): arg lists only, no console window, utf-8.

Every ffmpeg/ffprobe invocation in the app goes through ``run()`` so paths
with Arabic/spaces always work and failures carry a readable stderr tail.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
_TAIL_CHARS = 4000


class ProcError(RuntimeError):
    """A subprocess exited non-zero; ``stderr_tail`` holds its last output."""

    def __init__(self, args: list, returncode: int, stderr_tail: str):
        self.args_list = [str(a) for a in args]
        self.returncode = returncode
        self.stderr_tail = stderr_tail
        super().__init__(
            f"command failed ({returncode}): "
            f"{' '.join(self.args_list)!r}\n{stderr_tail}"
        )


def run(
    args: list[str | Path], timeout: float | None = None
) -> subprocess.CompletedProcess:
    """Run ``args`` (no shell), return the CompletedProcess; raise on != 0."""
    argv = [str(a) for a in args]
    try:
        cp = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=_NO_WINDOW,
        )
    except subprocess.TimeoutExpired as exc:
        raise ProcError(argv, -1, f"timed out after {timeout}s") from exc
    if cp.returncode != 0:
        raise ProcError(argv, cp.returncode, (cp.stderr or "")[-_TAIL_CHARS:])
    return cp
