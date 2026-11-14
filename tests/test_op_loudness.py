"""M4a Task 4: loudness — two-pass loudnorm, true-peak cap, quiet-input
clamp, cancel (Review Focus 1 + 3)."""
import threading
import time
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from backend.core.ffmpeg import find_ffmpeg
from backend.core.proc import run
from backend.media.decode import decode_to_wav
from backend.media.loudness import integrated_lufs, true_peak_db
from backend.media.probe import probe
from backend.operations.base import OpContext
from backend.operations.impl import IMPLEMENTATIONS
from backend.pipeline.types import CancelledError

SR = 48000
GENERATED = Path(__file__).parent / "fixtures" / "generated"
SPEECH = GENERATED / "speech.wav"
REC = "Recording.m4a"


def _ctx(tmp_path: Path, in_wav: Path, reports: list | None = None,
         cancel=None) -> OpContext:
    info = sf.info(str(in_wav))
    return OpContext(
        work_dir=tmp_path,
        device="cpu",
        duration_s=info.frames / info.samplerate,
        sample_rate=info.samplerate,
        channels=info.channels,
        report=(
            (lambda p, m: reports.append((p, m)))
            if reports is not None else (lambda p, m: None)
        ),
        should_cancel=cancel or (lambda: False),
    )


def _run_loudness(in_wav: Path, tmp_path: Path, params: dict,
                  reports: list | None = None, cancel=None):
    ctx = _ctx(tmp_path, in_wav, reports=reports, cancel=cancel)
    return IMPLEMENTATIONS["loudness"](in_wav, params, ctx)


def _decode_real(real_dir: Path, tmp_path: Path, name: str = REC) -> Path:
    src = real_dir / name
    p = probe(src)
    dst = tmp_path / "decoded.wav"
    decode_to_wav(src, dst, p.audio[0].index)
    return dst


def test_loudness_podcast_target_real(tmp_path, real_dir):
    """Review Focus: real recording → −16 ±0.5 LUFS, TP ≤ −0.9 dBTP."""
    src = _decode_real(real_dir, tmp_path)
    reports: list = []
    res = _run_loudness(src, tmp_path, {"target": "podcast"},
                        reports=reports)
    lufs = integrated_lufs(res.path)
    assert lufs is not None
    assert abs(lufs - (-16.0)) <= 0.5, f"integrated {lufs} LUFS"
    assert true_peak_db(res.path) <= -0.9
    # two-pass progress: something above 50 % was reported
    pcts = [p for p, _ in reports]
    assert pcts and max(pcts) > 0.5


def test_loudness_youtube_and_custom(tmp_path):
    # ~-30 LUFS sine: both targets reachable without hitting the TP cap.
    t = np.arange(int(SR * 3)) / SR
    quiet = (0.05 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    src = tmp_path / "mid.wav"
    sf.write(str(src), quiet, SR, subtype="FLOAT")

    res = _run_loudness(src, tmp_path, {"target": "youtube"})
    assert abs(integrated_lufs(res.path) - (-14.0)) <= 0.5
    res = _run_loudness(
        src, tmp_path, {"target": "custom", "lufs": -23}
    )
    assert abs(integrated_lufs(res.path) - (-23.0)) <= 0.5
    assert true_peak_db(res.path) <= -0.9


def test_loudness_very_quiet_file_clamps(tmp_path):
    """Review Focus 1: −60 LUFS input → no gain explosion; message set;
    output TP ≤ −1 dBTP."""
    t = np.arange(int(SR * 3)) / SR
    quiet = (0.0015 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    src = tmp_path / "quiet.wav"
    sf.write(str(src), quiet, SR, subtype="FLOAT")
    measured = integrated_lufs(src)
    assert measured is not None and measured < -50

    res = _run_loudness(src, tmp_path, {"target": "podcast"})

    assert res.message == (
        "Very quiet recording — raised as far as safely possible"
    )
    assert true_peak_db(res.path) <= -0.9
    out_lufs = integrated_lufs(res.path)
    # clamped to measured + 30 — never reaches −16 but stays sane
    assert out_lufs is not None and out_lufs < -20


def test_loudness_preserves_length_and_channels(tmp_path, real_dir):
    src = _decode_real(real_dir, tmp_path, "Recording (2).m4a")
    res = _run_loudness(src, tmp_path, {"target": "podcast"})
    info_in, info_out = sf.info(str(src)), sf.info(str(res.path))
    assert abs(info_out.frames - info_in.frames) <= 1
    assert info_out.channels == info_in.channels


def test_loudness_mono_stays_mono(tmp_path):
    info = sf.info(str(SPEECH))
    assert info.channels == 1
    res = _run_loudness(SPEECH, tmp_path, {"target": "podcast"})
    assert sf.info(str(res.path)).channels == 1


def test_true_peak_db_helper(tmp_path):
    t = np.arange(int(SR * 1)) / SR
    hot = (0.9 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    src = tmp_path / "hot.wav"
    sf.write(str(src), hot, SR, subtype="FLOAT")
    tp = true_peak_db(src)
    assert -1.5 <= tp <= -0.5  # sine at 0.9 ≈ −0.9 dBFS


def test_loudness_cancel_kills_ffmpeg(tmp_path):
    """Review Focus 3: cancel during a long loudness pass → ffmpeg gone
    within 1 s, CancelledError, no partial stage file."""
    import psutil

    long_wav = tmp_path / "long.wav"
    run([
        find_ffmpeg(), "-nostdin", "-hide_banner", "-y",
        "-f", "lavfi", "-i", "anoisesrc=color=pink:duration=600",
        "-ar", "48000", "-ac", "2", "-c:a", "pcm_f32le", long_wav,
    ])
    cancel = threading.Event()
    errors: list[BaseException] = []

    def go():
        try:
            _run_loudness(long_wav, tmp_path, {"target": "podcast"},
                          cancel=cancel.is_set)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    t = threading.Thread(target=go)
    t.start()
    time.sleep(1.0)
    cancel.set()
    t0 = time.monotonic()
    t.join(timeout=10)
    gone_in = time.monotonic() - t0

    assert not t.is_alive()
    assert errors and isinstance(errors[0], CancelledError)
    assert gone_in < 1.0, f"ffmpeg still alive {gone_in:.2f}s after cancel"
    assert not (tmp_path / "stage_80_loudness.wav").exists()
    leftovers = [
        p for p in psutil.Process().children(recursive=True)
        if "ffmpeg" in p.name().lower()
    ]
    assert leftovers == []
