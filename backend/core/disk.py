"""M10 T5: pre-queue peak-disk estimates (SPEC §4 disk safety).

A job's footprint in ``data/jobs/<id>/`` is dominated by 48 kHz float32
WAV intermediates — decode output plus at least one op handoff — the
final output encode, and, when video is kept, a re-encode worst case of
the source size plus the audio it muxes. The estimate is deliberately
pessimistic: refusing early beats filling the disk mid-run.
"""
from __future__ import annotations

PCM_RATE = 48000
PCM_BPS = 4  # float32 samples


def estimate_peak_bytes(
    duration_s: float,
    channels: int,
    has_video: bool,
    source_size_bytes: int = 0,
) -> int:
    """Estimated peak bytes a job may need inside ``data/jobs/<id>/``."""
    pcm = max(0.0, duration_s) * PCM_RATE * PCM_BPS * max(1, channels)
    intermediates = 2 * pcm
    audio_out = pcm
    video = pcm + max(0, source_size_bytes) if has_video else 0
    return int(intermediates + audio_out + video)


def needed_bytes_for_job(
    probe: dict,
    kind: str,
    preview: dict | None,
    output: dict,
    audio_stream_index: int,
    source_size_bytes: int = 0,
) -> int:
    """Estimate for one job against a stored probe_json dict."""
    try:
        duration_s = float(probe.get("duration_s") or 0.0)
    except (TypeError, ValueError):
        duration_s = 0.0
    if preview:
        try:
            duration_s = max(
                0.0,
                float(preview["end_s"]) - float(preview["start_s"]),
            )
        except (KeyError, TypeError, ValueError):
            pass
    channels = 2
    for a in probe.get("audio") or []:
        if int(a.get("index") or 0) == audio_stream_index:
            try:
                channels = int(a.get("channels") or 2)
            except (TypeError, ValueError):
                channels = 2
            break
    wants_video = (
        kind == "full"
        and bool((output or {}).get("keep_video", True))
        and bool(probe.get("video"))
    )
    return estimate_peak_bytes(
        duration_s, channels, wants_video, source_size_bytes
    )
