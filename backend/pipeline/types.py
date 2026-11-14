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


ProgressCb = Callable[[Progress], None]
CancelCheck = Callable[[], bool]


class CancelledError(Exception):
    """Raised when ``should_cancel()`` reports a cancel between stages."""
