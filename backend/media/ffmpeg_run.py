"""Progress-aware, cancellable ffmpeg runner (M4a Task 1).

Every filter op goes through ``run_ffmpeg``: the standard flag set
(``-nostdin -hide_banner -nostats -progress pipe:1 -y``), ``out_time``-driven
percent callbacks, and a 0.2 s cancel poll that kills ffmpeg and removes the
partial output. ``run_filter`` is the one-filter convenience wrapper.
"""
from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

from backend.core.ffmpeg import find_ffmpeg
from backend.core.proc import ProcError
from backend.operations.base import OpContext
from backend.pipeline.types import CancelledError

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
_POLL_S = 0.2          # >= 4 cancel checks per second (M4a global constraints)
_STATS_PERIOD_S = 0.25  # -progress update cadence
_TAIL_CHARS = 4000


def _drain(stream, sink: list[str]) -> None:
    try:
        for line in iter(stream.readline, ""):
            sink.append(line)
    finally:
        stream.close()


def _out_time_us(line: str) -> int | None:
    """Parse ``out_time_us=``/``out_time_ms=`` progress lines (both keys were
    emitted under different ffmpeg versions; the value is microseconds)."""
    for key in ("out_time_us=", "out_time_ms="):
        if line.startswith(key):
            try:
                return int(line[len(key):].strip())
            except ValueError:
                return None
    return None


def run_ffmpeg(
    args: list[str | Path],
    duration_s: float | None,
    on_pct: Callable[[float], None],
    should_cancel: Callable[[], bool],
    out_path: Path | None = None,
) -> str:
    """Run ffmpeg with ``args`` (everything after the binary name).

    ``on_pct`` gets 0..100 values parsed from ``-progress`` output when
    ``duration_s`` is known, then a final 100 on success. On cancel: kill the
    process, delete ``out_path``, raise ``CancelledError``. Non-zero exit →
    ``ProcError`` with the stderr tail. Returns the full stderr text.
    """
    argv = [
        str(find_ffmpeg()),
        "-nostdin", "-hide_banner", "-nostats",
        "-stats_period", str(_STATS_PERIOD_S),
        "-progress", "pipe:1", "-y",
        *[str(a) for a in args],
    ]
    proc = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_NO_WINDOW,
    )
    out_lines: list[str] = []
    err_lines: list[str] = []
    threads = [
        threading.Thread(target=_drain, args=(proc.stdout, out_lines),
                         daemon=True),
        threading.Thread(target=_drain, args=(proc.stderr, err_lines),
                         daemon=True),
    ]
    for t in threads:
        t.start()

    last_pct = 0.0

    def consume(upto: int) -> int:
        nonlocal last_pct
        for line in out_lines[upto:]:
            upto += 1
            if duration_s is None:
                continue
            us = _out_time_us(line)
            if us is not None and duration_s > 0:
                pct = min(99.9, max(0.0, us / (duration_s * 1e6) * 100.0))
                if pct > last_pct:
                    last_pct = pct
                    on_pct(pct)
        return upto

    consumed = 0
    cancelled = False
    try:
        while proc.poll() is None:
            if should_cancel():
                cancelled = True
                break
            consumed = consume(consumed)
            time.sleep(_POLL_S)
        consumed = consume(consumed)  # flush lines written before exit
        if cancelled or should_cancel():
            cancelled = True
    finally:
        if cancelled:
            proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        for t in threads:
            t.join(timeout=5)

    if cancelled:
        if out_path is not None:
            Path(out_path).unlink(missing_ok=True)
        raise CancelledError()

    stderr = "".join(err_lines)
    if proc.returncode != 0:
        if out_path is not None:
            Path(out_path).unlink(missing_ok=True)
        raise ProcError(argv, proc.returncode, stderr[-_TAIL_CHARS:])
    on_pct(100.0)
    return stderr


def run_filter(
    in_wav: Path,
    out_wav: Path,
    afilter: str,
    ctx: OpContext,
    extra: list[str] | None = None,
) -> str:
    """One ``-af`` graph between two 48 kHz float32 WAVs, with progress and
    cancellation from ``ctx``. ``extra`` are inserted before the codec args
    (e.g. channel layouts). Returns ffmpeg's stderr."""
    args: list[str | Path] = [
        "-i", in_wav,
        "-af", afilter,
        *(extra or []),
        "-ar", "48000",
        "-c:a", "pcm_f32le",
        out_wav,
    ]
    return run_ffmpeg(
        args,
        duration_s=ctx.duration_s,
        on_pct=lambda p: ctx.report(p / 100.0, None),
        should_cancel=ctx.should_cancel,
        out_path=out_wav,
    )
