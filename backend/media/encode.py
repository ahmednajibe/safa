"""Final encoders, browser playback copy, and video remux (-c:v copy)."""
from __future__ import annotations

from pathlib import Path

from backend.core.ffmpeg import find_ffmpeg
from backend.core.proc import run

AUDIO_FORMATS: dict[str, tuple[str, list[str]]] = {
    "wav": (".wav", ["-acodec", "pcm_s24le"]),
    "flac": (".flac", ["-acodec", "flac"]),
    "mp3": (".mp3", ["-acodec", "libmp3lame", "-b:a", "320k"]),
    "aac": (".m4a", ["-acodec", "aac", "-b:a", "256k"]),
}

_MP4_CODECS = {"h264", "hevc", "av1", "mpeg4"}


def encode_audio(src_wav: Path, dst_stem: Path, fmt: str) -> Path:
    """Encode ``src_wav`` to ``fmt``; returns the path with real extension."""
    ext, codec_args = AUDIO_FORMATS[fmt]
    dst = Path(str(dst_stem) + ext)
    dst.parent.mkdir(parents=True, exist_ok=True)
    run([find_ffmpeg(), "-hide_banner", "-y", "-i", src_wav, *codec_args, dst])
    return dst


def video_container_for(codec: str) -> str:
    """Output container for a copied video stream of ``codec``."""
    return "mp4" if codec in _MP4_CODECS else "mkv"


def remux_video(
    video_src: Path,
    video_stream_index: int,
    audio_wav: Path,
    dst_stem: Path,
    video_codec: str,
) -> Path:
    """Copy the video stream and mux in the processed audio (AAC).

    No ``-shortest``: the video stream fully determines duration (output is
    never shorter than the video by more than a frame).
    """
    container = video_container_for(video_codec)
    dst = Path(f"{dst_stem}.{container}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    args = [
        find_ffmpeg(), "-hide_banner", "-y",
        "-i", video_src,
        "-i", audio_wav,
        "-map", f"0:{video_stream_index}",
        "-map", "1:a:0",
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "256k",
        "-map_metadata", "0",
    ]
    if container == "mp4":
        args += ["-movflags", "+faststart"]
    args += [dst]
    run(args)
    return dst


def make_playback_copy(src_wav: Path, dst: Path) -> Path:
    """Small AAC 192k .m4a the browser player can stream."""
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    run([
        find_ffmpeg(), "-hide_banner", "-y",
        "-i", src_wav, "-acodec", "aac", "-b:a", "192k", dst,
    ])
    return dst
