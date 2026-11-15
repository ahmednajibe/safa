"""Decode any audio stream to the working format: 48 kHz float32 WAV."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import soundfile as sf

from typing import Callable

from backend.core.ffmpeg import find_ffmpeg
from backend.core.proc import run
from backend.media.ffmpeg_run import run_ffmpeg
from backend.media.probe import probe
from backend.pipeline.types import CancelledError


@dataclass
class AudioInfo:
    path: Path
    sample_rate: int
    channels: int
    frames: int

    @property
    def duration_s(self) -> float:
        return self.frames / self.sample_rate if self.sample_rate else 0.0


def _source_channels(src: Path, stream_index: int) -> int:
    for a in probe(src).audio:
        if a.index == stream_index:
            return a.channels
    return 0


def decode_to_wav(
    src: Path,
    dst: Path,
    stream_index: int,
    sample_rate: int = 48000,
    start_s: float | None = None,
    end_s: float | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> AudioInfo:
    """Decode ``src``'s audio stream ``stream_index`` to float32 WAV at ``dst``.

    >2 channels are downmixed to stereo; mono/stereo keep their layout.
    ``start_s``/``end_s`` select a region (input seeking — sample accurate on
    the fixture set and much faster on long files). ``should_cancel`` kills
    ffmpeg mid-run and removes the partial file (R1 B1/B11).
    """
    dst = Path(dst)
    if should_cancel is not None and should_cancel():
        raise CancelledError()
    dst.parent.mkdir(parents=True, exist_ok=True)
    args: list = []
    if start_s is not None:
        args += ["-ss", f"{start_s}"]
    if end_s is not None:
        args += ["-to", f"{end_s}"]
    args += [
        "-i", src,
        "-map", f"0:{stream_index}",
        "-vn",
        "-acodec", "pcm_f32le",
        "-ar", str(sample_rate),
    ]
    if _source_channels(src, stream_index) > 2:
        args += ["-ac", "2"]
    args += [dst]
    if should_cancel is None:
        run([find_ffmpeg(), "-hide_banner", "-y", *args])
    else:
        run_ffmpeg(
            args,
            duration_s=None,
            on_pct=lambda p: None,
            should_cancel=should_cancel,
            out_path=dst,
        )
    info = sf.info(dst)
    return AudioInfo(
        path=dst,
        sample_rate=info.samplerate,
        channels=info.channels,
        frames=info.frames,
    )
