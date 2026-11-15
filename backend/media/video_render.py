"""Video re-render from the timeline map (M4b plan Task 3).

When the timeline is not identity the video can't be stream-copied: one
ffmpeg pass selects the kept frames (``fps`` → ``select`` → ``setpts``)
and re-encodes to h264 in mp4 with the processed audio muxed in. The
filtergraph is written to a file and passed with
``-filter_complex_script`` — hundreds of keep-intervals (M5's silence
cuts) would overflow Windows' 32 KB command-line limit as a literal.

Cuts happen on the video frame grid: ops snap the timeline to ``ctx.fps``
so ``between``-style selection lands on whole frames, and the comparison
is shrunk by a quarter frame on each side to survive float noise.

Encoder per SPEC §8/§9: ``h264_nvenc`` on gpu jobs when present
(``-preset p5 -cq 20 -b:v 0``), otherwise ``libx264 -preset medium
-crf 20``; a gpu job whose nvenc run fails retries once on libx264 and
reports the encoder actually used. ``cpu`` jobs never touch nvenc.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Literal

from backend.core.ffmpeg import has_encoder
from backend.core.proc import ProcError
from backend.media.ffmpeg_run import run_ffmpeg
from backend.media.timeline import Timeline

_NVENC = ("h264_nvenc", ["-c:v", "h264_nvenc", "-preset", "p5",
                         "-cq", "20", "-b:v", "0"])
_X264 = ("libx264", ["-c:v", "libx264", "-preset", "medium", "-crf", "20"])


def _fmt(x: float) -> str:
    s = f"{x:.9f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def _encoder_for(device: Literal["gpu", "cpu"]) -> tuple[str, list[str]]:
    if device == "gpu" and has_encoder(_NVENC[0]):
        return _NVENC
    return _X264


def _global_speed(timeline: Timeline) -> float:
    """Single speed factor for the whole map (this milestone's limit)."""
    speeds = {round(s.speed, 6) for s in timeline.segments}
    if len(speeds) == 1:
        return speeds.pop()
    kept = sum(b - a for a, b in timeline.kept_spans_src())
    return kept / timeline.out_duration if timeline.out_duration else 1.0


def _filter_script(timeline: Timeline, stream_index: int, fps: float) -> str:
    # Shrink each kept span by a quarter frame so float noise at grid
    # boundaries can't flip a frame in or out of the selection.
    e = 0.25 / fps
    terms = "+".join(
        f"(gte(t,{_fmt(a - e)})*lt(t,{_fmt(b - e)}))"
        for a, b in timeline.kept_spans_src()
    )
    if not terms:
        raise ValueError("timeline keeps no video frames")
    chain = (
        f"[0:{stream_index}]fps={_fmt(fps)},"
        f"select='{terms}',setpts=N/({_fmt(fps)}*TB)"
    )
    speed = _global_speed(timeline)
    if abs(speed - 1.0) > 1e-9:
        # R1 B6: rescale timestamps, then resample to the *source* rate —
        # fps*speed would emit 31.25/37.5/120 fps containers.
        chain += f",setpts=PTS/{_fmt(speed)},fps={_fmt(fps)}"
    return chain + "[v]\n"


def render_video(
    video_src: Path,
    video_stream_index: int,
    audio_wav: Path,
    timeline: Timeline,
    dst_stem: Path,
    device: Literal["gpu", "cpu"],
    fps: float,
    report: Callable[[float, str | None], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    script_path: Path | None = None,
) -> tuple[Path, str]:
    """Re-encode ``video_src``'s video through ``timeline`` + mux audio.

    Returns ``(output_path, encoder_used)`` — always an h264 ``.mp4`` (we
    re-encode, so the container is mp4 even for mkv/exotic inputs).
    """
    dst = Path(f"{dst_stem}.mp4")
    dst.parent.mkdir(parents=True, exist_ok=True)
    script = Path(script_path) if script_path else dst.parent / "video_filter.txt"
    script.write_text(
        _filter_script(timeline, video_stream_index, fps), encoding="utf-8"
    )

    def _args(enc_args: list[str]) -> list:
        return [
            "-i", video_src,
            "-i", audio_wav,
            "-filter_complex_script", script,
            "-map", "[v]",
            "-map", "1:a:0",
            *enc_args,
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "256k",
            "-movflags", "+faststart",
            dst,
        ]

    emit = report or (lambda p, m=None: None)
    cancel = should_cancel or (lambda: False)
    name, enc_args = _encoder_for(device)
    try:
        run_ffmpeg(
            _args(enc_args), duration_s=timeline.out_duration,
            on_pct=lambda p: emit(p / 100.0, None),
            should_cancel=cancel, out_path=dst,
        )
        return dst, name
    except ProcError:
        if name == _X264[0]:
            raise
        # nvenc failed at runtime → one retry on libx264 (SPEC §8)
        name, enc_args = _X264
        run_ffmpeg(
            _args(enc_args), duration_s=timeline.out_duration,
            on_pct=lambda p: emit(p / 100.0, None),
            should_cancel=cancel, out_path=dst,
        )
        return dst, name
