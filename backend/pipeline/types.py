"""Shared pipeline types (M3a plan, SPEC §5/§9)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

JobDevice = Literal["gpu", "cpu"]
AudioFormat = Literal["wav", "flac", "mp3", "aac"]
FileKind = Literal["audio", "video", "subtitle", "stem"]


@dataclass
class OutputSpec:
    audio_format: AudioFormat = "wav"
    keep_video: bool = True


@dataclass
class OutputFile:
    name: str
    kind: FileKind
    path: Path
    size_bytes: int


@dataclass
class Progress:
    stage: str
    stage_idx: int
    total_stages: int
    stage_pct: float | None
    overall_pct: float | None
    message: str | None = None
    device: str | None = None
    # M9a Task 0: machine-readable stage identity (op id / decode / encode /
    # video / done) — the UI translates it; ``stage`` stays English for logs.
    stage_id: str | None = None
    # Seconds remaining, from estimates × remaining stages refined by
    # measured stage seconds; None while unknown.
    eta_sec: float | None = None
    # SPEC §11: the English ``message`` is only a fallback — the frontend
    # translates via ``message_code`` + ``message_params``.
    message_code: str | None = None
    message_params: dict | None = None
    warning_code: str | None = None
    warning_params: dict | None = None
    # M6 Task 0: set only on an op stage's closing row — wall seconds and
    # the op's own fallback reason (distinct from the gpu_fallback warning
    # the runner derives) so the worker can persist the stage record.
    stage_seconds: float | None = None
    fallback_reason: str | None = None


ProgressCb = Callable[[Progress], None]
CancelCheck = Callable[[], bool]


class CancelledError(Exception):
    """Raised when ``should_cancel()`` reports a cancel between stages."""
