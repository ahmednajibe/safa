"""VAD-gated level statistics (M4d Task 1).

``vad_level_stats`` measures what the *input* actually sounds like:
``noise_floor_dbfs`` is the P75 RMS of VAD non-speech frames and
``speech_dbfs`` the P85 RMS of VAD speech frames — never all-frame
percentiles, which mistake quiet speech for floor once pauses are cut.
The floor uses the *upper* quantile of non-speech on purpose: it should
describe the audible noise bed that a gate must sit above, not the
deepest pause — files with digitally-muted gaps otherwise report
``-90 dB`` floors that drop the expander threshold under everything.
When VAD itself cannot run (no silero, degenerate input) the function
degrades to the same percentiles over all frames so callers still get
sane calibration values instead of a crash.

The pipeline shares these through ``OpContext.analysis`` — the first
stage that runs VAD (denoise → breaths → silence, whichever comes
first) writes both keys so ``enhance``, running after the silence cut,
still calibrates on pre-cut measurements.

The frame grid is computed streaming in 10 s blocks (peak RSS stays in
the MB range on hour-long files — the naive one-shot read + fancy-index
grid spiked a 30 min job to ~5 GiB).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

_WIN_S = 0.02       # 20 ms frames, like breaths' energy grid
_HOP_S = 0.01       # 10 ms hop
_BLOCK_S = 10.0     # streaming read block
_MIN_CLASS_FRAMES = 30  # <300 ms of a class → percentile unreliable


def _frame_rms_db(mono: np.ndarray, sr: int) -> np.ndarray:
    """Per-frame RMS dBFS on a 20 ms / 10 ms grid (in-memory version,
    used by tests on short arrays)."""
    win = max(1, int(_WIN_S * sr))
    hop = max(1, int(_HOP_S * sr))
    if len(mono) < win:
        rms = float(np.sqrt(np.mean(mono ** 2) + 1e-12))
        return np.array([20.0 * np.log10(rms + 1e-9)])
    n = 1 + (len(mono) - win) // hop
    idx = hop * np.arange(n)[:, None] + np.arange(win)[None, :]
    rms = np.sqrt((mono[idx] ** 2).mean(axis=1) + 1e-12)
    return 20.0 * np.log10(rms + 1e-9)


def _frame_rms_db_stream(wav: Path, sr: int) -> np.ndarray:
    """Same grid as ``_frame_rms_db`` but read in 10 s blocks with a
    ``win``-sample carry, so RSS doesn't scale with file length."""
    win = max(1, int(_WIN_S * sr))
    hop = max(1, int(_HOP_S * sr))
    frames: list[np.ndarray] = []
    tail = np.empty(0, dtype=np.float64)
    with sf.SoundFile(str(wav)) as f:
        while True:
            blk = f.read(int(_BLOCK_S * sr), dtype="float64",
                         always_2d=True)
            if not len(blk):
                break
            buf = np.concatenate([tail, blk.mean(axis=1)])
            n = (len(buf) - win) // hop + 1
            if n > 0:
                idx = hop * np.arange(n)[:, None] + np.arange(win)[None, :]
                rms = np.sqrt((buf[idx] ** 2).mean(axis=1) + 1e-12)
                frames.append(20.0 * np.log10(rms + 1e-9))
                tail = buf[n * hop:]
            else:
                tail = buf
    if not frames:
        if len(tail):
            rms = float(np.sqrt(np.mean(tail ** 2) + 1e-12))
            return np.array([20.0 * np.log10(rms + 1e-9)])
        return np.empty(0)
    return np.concatenate(frames)


def _speech_mask(
    n_frames: int,
    intervals: list[tuple[float, float]],
    total_s: float,
) -> np.ndarray:
    """Frame-centre-in-interval mask on the 10 ms hop grid."""
    mask = np.zeros(n_frames, dtype=bool)
    centres = (np.arange(n_frames) * _HOP_S + _WIN_S / 2)
    for a, b in intervals:
        mask |= (centres >= a) & (centres < b)
    return mask


def vad_level_stats(
    wav: Path,
    speech: list[tuple[float, float]] | None = None,
    should_cancel=None,
) -> dict[str, float]:
    """``{noise_floor_dbfs, speech_dbfs, speech_intervals}`` for ``wav``.

    ``speech`` may carry precomputed VAD intervals (a stage that already
    ran the VAD passes them in so it doesn't run twice). When the file
    has <300 ms of either class the missing percentile falls back to the
    same percentile over all frames.
    """
    if should_cancel is not None and should_cancel():
        from backend.pipeline.types import CancelledError

        raise CancelledError()
    info = sf.info(str(wav))
    sr = int(info.samplerate)
    if speech is None:
        try:
            from backend.media.silence_detect import speech_intervals_vad

            speech = speech_intervals_vad(
                wav, should_cancel=should_cancel
            )
        except Exception:
            speech = []
    frames = _frame_rms_db_stream(wav, sr)
    if frames.size == 0:
        return {
            "noise_floor_dbfs": -80.0,
            "speech_dbfs": -80.0,
            "speech_intervals": speech or [],
        }
    mask = _speech_mask(
        frames.size, speech or [], info.frames / sr
    )
    non_speech = frames[~mask]
    voiced = frames[mask]
    all_p10 = float(np.percentile(frames, 10))
    all_p85 = float(np.percentile(frames, 85))
    floor = (
        float(np.percentile(non_speech, 75))
        if non_speech.size >= _MIN_CLASS_FRAMES
        else all_p10
    )
    level = (
        float(np.percentile(voiced, 85))
        if voiced.size >= _MIN_CLASS_FRAMES
        else all_p85
    )
    return {
        "noise_floor_dbfs": floor,
        "speech_dbfs": level,
        "speech_intervals": speech or [],
    }


def ensure_vad_stats(
    wav: Path,
    analysis: dict,
    speech: list[tuple[float, float]] | None = None,
    should_cancel=None,
) -> dict[str, float]:
    """Fill ``analysis`` once (first VAD stage wins) and return the stats.

    Besides the two level keys this also publishes ``speech_intervals``
    in THIS input's time + ``speech_intervals_dur`` — ``enhance`` uses
    them to bypass its expander inside speech (M4d fix round). A
    length-changing stage must re-express or discard them (``silence``
    rewrites them in output time after the cut)."""
    if "noise_floor_dbfs" in analysis and "speech_dbfs" in analysis:
        return {
            "noise_floor_dbfs": analysis["noise_floor_dbfs"],
            "speech_dbfs": analysis["speech_dbfs"],
        }
    stats = vad_level_stats(wav, speech=speech, should_cancel=should_cancel)
    analysis.setdefault("noise_floor_dbfs", stats["noise_floor_dbfs"])
    analysis.setdefault("speech_dbfs", stats["speech_dbfs"])
    analysis.setdefault("speech_intervals", stats["speech_intervals"])
    info = sf.info(str(wav))
    analysis.setdefault(
        "speech_intervals_dur", info.frames / info.samplerate
    )
    return stats
