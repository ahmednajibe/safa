"""Coarse runtime estimates (SPEC §9): per-engine real-time factors from
docs/FEASIBILITY.md (measured on ~5–6 s clips — rough on purpose)."""
from __future__ import annotations

from backend.core.capabilities import Capabilities, detect_capabilities
from backend.pipeline.device import resolve_device
from backend.pipeline.types import JobDevice

# (low_rtf, high_rtf): processing_seconds per second of input audio.
_RTF: dict[tuple[str, str], tuple[float, float]] = {
    ("separate:htdemucs", "gpu"): (0.4, 0.8),
    ("separate:htdemucs", "cpu"): (3.5, 6.5),
    ("separate:kim_vocal_2", "gpu"): (0.6, 1.2),
    ("separate:kim_vocal_2", "cpu"): (2.0, 4.0),
    ("denoise:ai", "gpu"): (0.08, 0.2),
    ("denoise:ai", "cpu"): (0.2, 0.45),
    ("denoise:classic", "cpu"): (0.02, 0.08),
    # subtitles: measured RTFs on this machine (docs/FEASIBILITY.md):
    # small GPU ~0.16–0.5× / CPU ~2.3×; turbo GPU ~0.2–0.35× on the
    # 19–33 s real recordings (6.8–10.1 s incl. load), CPU unmeasured.
    ("subtitles:fast", "gpu"): (0.15, 0.7),
    ("subtitles:fast", "cpu"): (1.5, 3.5),
    ("subtitles:accurate", "gpu"): (0.25, 0.9),
    ("subtitles:accurate", "cpu"): (3.0, 7.0),
    ("silence", "cpu"): (0.1, 0.3),
    ("enhance", "cpu"): (0.04, 0.12),
    ("loudness", "cpu"): (0.04, 0.12),  # two passes
    ("speed", "cpu"): (0.02, 0.08),
}
_DEFAULT_RTF = (0.02, 0.08)

# Fixed decode + encode + remux overhead.
_OVERHEAD = (1.0, 8.0)


def _rtf_key(op_id: str, params: dict) -> str:
    if op_id == "separate":
        return f"separate:{(params or {}).get('model', 'htdemucs')}"
    if op_id == "denoise":
        return f"denoise:{(params or {}).get('engine', 'ai')}"
    if op_id == "subtitles":
        return f"subtitles:{(params or {}).get('model', 'fast')}"
    return op_id


def estimate_seconds(
    duration_s: float,
    ops: list[tuple[str, dict]],
    device: JobDevice,
    caps: Capabilities | None = None,
) -> tuple[float, float]:
    """Rough (low, high) seconds for ``ops`` on ``duration_s`` of audio."""
    caps = caps or detect_capabilities()
    lo, hi = _OVERHEAD
    for op_id, params in ops:
        dev = resolve_device(device, op_id, params, caps)
        rtf = _RTF.get((_rtf_key(op_id, params), dev))
        if rtf is None:
            rtf = _RTF.get((op_id, dev), _DEFAULT_RTF)
        lo += duration_s * rtf[0]
        hi += duration_s * rtf[1]
    return lo, hi


# Ops that change the timeline — with video output they force a re-encode
# instead of a stream-copy remux (SPEC §3), so their video stage estimate is
# duration-based rather than fixed.
_TIMELINE_OPS = {"trim", "silence", "speed"}

# Rough per-device real-time factors for a video re-encode stage.
_VIDEO_RTF = {"gpu": 0.5, "cpu": 1.5}

# Correction applied to remaining estimates from measured stage seconds is
# clamped so one weird stage can't send the ETA to 0 or infinity.
_ETA_RATIO_MIN, _ETA_RATIO_MAX = 0.25, 4.0


def stage_estimates(
    duration_s: float,
    ops: list[tuple[str, dict]],
    device: JobDevice,
    want_video: bool,
    caps: Capabilities | None = None,
) -> list[float]:
    """Estimated seconds per pipeline stage in runner order:
    ``decode`` → one per op → ``encode`` → ``video``? (M9a Task 0).

    Rough on purpose — the same (low, high) RTF factors as
    :func:`estimate_seconds`, using the high end so the ETA doesn't
    under-promise. Fixed stages split the ``_OVERHEAD`` budget; a video
    re-encode is duration-based, a remux is a fixed sliver.
    """
    caps = caps or detect_capabilities()
    fixed = _OVERHEAD[1] / 2
    ests = [fixed]  # decode
    for op_id, params in ops:
        dev = resolve_device(device, op_id, params, caps)
        rtf = _RTF.get((_rtf_key(op_id, params), dev))
        if rtf is None:
            rtf = _RTF.get((op_id, dev), _DEFAULT_RTF)
        ests.append(duration_s * rtf[1])
    ests.append(fixed)  # encode
    if want_video:
        reencode = any(op_id in _TIMELINE_OPS for op_id, _ in ops)
        if reencode:
            ests.append(duration_s * _VIDEO_RTF[device])
        else:
            ests.append(fixed)  # remux
    return ests


def eta_remaining(
    ests: list[float],
    current_idx: int,
    current_pct: float | None,
    measured: dict[int, float],
) -> float | None:
    """Estimated seconds left in the pipeline (M9a Task 0).

    ``ests`` is one estimated duration per stage (runner order);
    ``measured`` maps finished stage indexes to their wall seconds and
    rescales the remaining estimates by the observed ratio (clamped).
    ``None`` when there is nothing to base an estimate on.
    """
    if not ests:
        return None
    if current_idx >= len(ests):
        return 0.0
    if current_idx < 0:
        current_idx = 0
    done = [i for i in measured if 0 <= i < len(ests)]
    ratio = 1.0
    if done:
        est_done = sum(ests[i] for i in done)
        if est_done > 0:
            ratio = sum(measured[i] for i in done) / est_done
            ratio = min(max(ratio, _ETA_RATIO_MIN), _ETA_RATIO_MAX)
    pct = current_pct if current_pct is not None else 0.0
    remaining = ests[current_idx] * max(0.0, 1.0 - min(pct, 1.0))
    remaining += sum(ests[current_idx + 1:])
    return ratio * remaining


def bucket_for(high_s: float) -> str:
    """UI bucket (UI_DESIGN §6 dock subline): coarse on purpose."""
    code, minutes = bucket_code_for(high_s)
    if code == "under_1m":
        return "Under a minute"
    if code == "over_30m":
        return "Over 30 min"
    return f"About {minutes} min"


def bucket_code_for(high_s: float) -> tuple[str, int | None]:
    """SPEC §11 machine bucket: (bucket_code, minutes|None)."""
    if high_s < 60:
        return "under_1m", None
    if high_s > 30 * 60:
        return "over_30m", None
    return "about_n_m", max(2, int(high_s / 60 + 0.5))
