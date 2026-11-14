"""ffprobe wrapper: typed stream/format info for uploads and the pipeline."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from backend.core.ffmpeg import find_ffprobe
from backend.core.proc import ProcError, run


class ProbeError(RuntimeError):
    """ffprobe could not read the file (damaged / not media / missing)."""


@dataclass
class AudioStream:
    index: int
    codec: str
    sample_rate: int
    channels: int
    channel_layout: str | None
    language: str | None
    title: str | None
    duration_s: float | None


@dataclass
class VideoStream:
    index: int
    codec: str
    width: int
    height: int
    fps: float | None
    duration_s: float | None


@dataclass
class MediaProbe:
    duration_s: float
    format_name: str
    size_bytes: int
    audio: list[AudioStream]
    video: list[VideoStream]

    @property
    def has_video(self) -> bool:
        return bool(self.video)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["has_video"] = self.has_video
        return d


def _fps(raw: str | None) -> float | None:
    if not raw or raw == "0/0":
        return None
    if "/" in raw:
        num, den = raw.split("/", 1)
        try:
            den_f = float(den)
            return float(num) / den_f if den_f else None
        except ValueError:
            return None
    try:
        return float(raw)
    except ValueError:
        return None


def _f(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def probe(path: Path) -> MediaProbe:
    """Probe ``path`` with ffprobe; raise ProbeError on failure."""
    args = [
        find_ffprobe(),
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        path,
    ]
    try:
        cp = run(args, timeout=30)
        data = json.loads(cp.stdout or "{}")
    except ProcError as exc:
        raise ProbeError(f"ffprobe failed for {path.name}: {exc.stderr_tail}") from exc
    except (json.JSONDecodeError, OSError) as exc:
        raise ProbeError(f"ffprobe failed for {path.name}: {exc}") from exc

    fmt = data.get("format") or {}
    audio: list[AudioStream] = []
    video: list[VideoStream] = []
    for s in data.get("streams") or []:
        kind = s.get("codec_type")
        if kind == "audio":
            audio.append(
                AudioStream(
                    index=s["index"],
                    codec=s.get("codec_name") or "unknown",
                    sample_rate=int(s.get("sample_rate") or 0),
                    channels=int(s.get("channels") or 0),
                    channel_layout=s.get("channel_layout"),
                    language=(s.get("tags") or {}).get("language"),
                    title=(s.get("tags") or {}).get("title"),
                    duration_s=_f(s.get("duration")),
                )
            )
        elif kind == "video":
            # Attached pictures (cover art) are not real video streams.
            if (s.get("disposition") or {}).get("attached_pic"):
                continue
            video.append(
                VideoStream(
                    index=s["index"],
                    codec=s.get("codec_name") or "unknown",
                    width=int(s.get("width") or 0),
                    height=int(s.get("height") or 0),
                    fps=_fps(s.get("avg_frame_rate") or s.get("r_frame_rate")),
                    duration_s=_f(s.get("duration")),
                )
            )
    if not audio and not video:
        raise ProbeError(f"no streams found in {path.name}")
    return MediaProbe(
        duration_s=_f(fmt.get("duration")) or 0.0,
        format_name=fmt.get("format_name") or "",
        size_bytes=int(fmt.get("size") or Path(path).stat().st_size),
        audio=audio,
        video=video,
    )
