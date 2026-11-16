"""M7 Task 1: audio-separator engine wrapper (backend/media/separator.py).

Logic tests use a fake engine (OOM ladder, silent-window skip, stitching);
the real htdemucs / Kim_Vocal_2 models run the quality checks (SI-SDR,
karaoke metrics, the 48→44.1→48 kHz round-trip, VRAM/RAM bounds).
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from backend.core.capabilities import detect_capabilities  # noqa: E402
from backend.core.gpu import VramSampler  # noqa: E402
from backend.core.paths import FIXTURES_DIR  # noqa: E402
from backend.media import separator  # noqa: E402
from backend.media.separator import process_file  # noqa: E402
from backend.pipeline.device import gpu_usable  # noqa: E402
from tests.audio_metrics import si_sdr_db_multi  # noqa: E402

SEP_MIX = FIXTURES_DIR / "sep_mix.wav"
SEP_MUSIC = FIXTURES_DIR / "sep_music.wav"
SEP_VOICE = FIXTURES_DIR / "sep_voice.wav"
SR = 48000


def _has_gpu() -> bool:
    try:
        return gpu_usable(detect_capabilities())
    except Exception:
        return False


GPU = _has_gpu()


def _write(path: Path, x: np.ndarray, sr: int = SR) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(path, np.asarray(x, dtype=np.float32), sr, subtype="FLOAT")
    return path


def _load(path: Path) -> np.ndarray:
    x, _ = sf.read(str(path), dtype="float32", always_2d=True)
    return x


def _rms_db(x: np.ndarray) -> float:
    return float(20 * np.log10(np.sqrt((x ** 2).mean()) + 1e-12))


# ---------- fake engine seam --------------------------------------------------


class _FakeSep:
    """Engine double: stems = 0.5 * mix, written at the model rate."""

    def __init__(self, need):
        self.need = set(need)
        self.windows = 0
        self.win_channels: list[int] = []
        self.closed = False

    def separate_window(self, win_in: Path, wdir: Path) -> dict:
        i = self.windows
        self.windows += 1
        x, sr = sf.read(str(win_in), dtype="float32", always_2d=True)
        self.win_channels.append(x.shape[1])
        out = {}
        if "voice" in self.need:
            p = wdir / f"f{i}_v.wav"
            sf.write(str(p), x * 0.5, sr)
            out["voice"] = p
        if "music" in self.need:
            p = wdir / f"f{i}_m.wav"
            sf.write(str(p), x * 0.5, sr)
            out["music"] = p
        return out

    def close(self) -> None:
        self.closed = True


def _patch_engine(monkeypatch, calls: list, boom: dict | None = None):
    """Replace ``separator._make_engine`` with a recording fake.

    ``calls`` collects (model, device, demucs_segment, need) per attempt.
    ``boom`` maps device → exception raised inside ``separate_window``.
    ``engines`` collects the fake engines so tests can inspect calls.
    """
    engines: list[_FakeSep] = []

    def make(model, device, need, demucs_segment, report=None, should_cancel=None):
        calls.append(
            {
                "model": model,
                "device": device,
                "demucs_segment": demucs_segment,
                "need": set(need),
            }
        )
        eng = _FakeSep(need)
        exc = (boom or {}).get(device)
        if exc is not None:
            def boom_separate(win_in, wdir, _e=exc):
                raise _e

            eng.separate_window = boom_separate
        engines.append(eng)
        return eng

    monkeypatch.setattr(separator, "_make_engine", make)
    return engines


# ---------- pure helpers ------------------------------------------------------


def test_is_oom_message_substrings():
    assert separator._is_oom(RuntimeError("CUDA out of memory"))
    assert separator._is_oom(RuntimeError("CUDNN_STATUS_ALLOC_FAILED"))
    assert separator._is_oom(RuntimeError("Failed to allocate memory"))
    assert not separator._is_oom(RuntimeError("some other error"))
    assert not separator._is_oom(ValueError("bad params"))


def test_process_file_requires_a_stem(tmp_path):
    x = np.zeros((SR, 2), np.float32)
    _write(tmp_path / "in.wav", x)
    with pytest.raises(ValueError):
        process_file(
            tmp_path / "in.wav", None, None,
            model="htdemucs", device="cpu", work_dir=tmp_path / "w",
        )


def test_process_file_rejects_non_48k(tmp_path):
    x = np.zeros((44100, 2), np.float32)
    _write(tmp_path / "in.wav", x, sr=44100)
    with pytest.raises(AssertionError):
        process_file(
            tmp_path / "in.wav", tmp_path / "v.wav", None,
            model="htdemucs", device="cpu", work_dir=tmp_path / "w",
        )


def test_model_io_roundtrip_exact(tmp_path):
    """48 kHz block → 44.1 kHz model file → 48 kHz stem, exact frame count."""
    rng = np.random.default_rng(0)
    x = rng.standard_normal((SR, 2)).astype(np.float32) * 0.1
    win = separator._write_model_input(x, tmp_path / "w.wav")
    info = sf.info(str(win))
    assert info.samplerate == 44100
    assert info.channels == 2
    y = separator._read_stem(win, len(x), 2)
    assert y.shape == (len(x), 2)


def test_model_io_mono_becomes_stereo(tmp_path):
    rng = np.random.default_rng(1)
    x = rng.standard_normal((SR, 1)).astype(np.float32) * 0.1
    win = separator._write_model_input(x, tmp_path / "w.wav")
    assert sf.info(str(win)).channels == 2
    y = separator._read_stem(win, len(x), 1)
    assert y.shape == (len(x), 1)


def test_resample_preserves_tone_frequency(tmp_path):
    """1 kHz tone through the 48→44.1→48 round trip stays 1000 ± 2 Hz."""
    t = np.arange(SR) / SR
    x = np.stack([np.sin(2 * np.pi * 1000 * t)] * 2, axis=1).astype(np.float32)
    win = separator._write_model_input(x, tmp_path / "w.wav")
    y = separator._read_stem(win, len(x), 2)
    spec = np.abs(np.fft.rfft(y[:, 0]))
    f0 = np.fft.rfftfreq(len(y), 1 / SR)[spec.argmax()]
    assert abs(f0 - 1000) <= 2


# ---------- Separator construction constraints (argon_m7) --------------------


def test_new_separator_constraints_demucs(monkeypatch):
    """normalization/amplification/invert/segment must follow argon_m7."""
    import audio_separator.separator as asep

    captured = {}

    class Dummy:
        def __init__(self, **kw):
            captured.update(kw)

    monkeypatch.setattr(asep, "Separator", Dummy)
    separator._new_separator("htdemucs", "gpu", {"voice", "music"}, 7)
    assert captured["normalization_threshold"] == 1.0
    assert captured["amplification_threshold"] == 0.0
    assert captured["invert_using_spec"] is False
    assert captured["use_soundfile"] is True
    assert captured["output_format"] == "WAV"
    assert captured["demucs_params"]["segment_size"] == 7
    # R1 C3 weak test: determinism pins must be asserted here —
    # upstream defaults to shifts=2 (per-window random shift) and cuDNN
    # autotuning is non-deterministic across processes.
    assert captured["demucs_params"]["shifts"] == 0
    import torch

    assert torch.backends.cudnn.deterministic is True
    assert torch.backends.cudnn.benchmark is False
    assert captured["sample_rate"] == 44100


def test_new_separator_constraints_mdx(monkeypatch):
    import audio_separator.separator as asep

    captured = {}

    class Dummy:
        def __init__(self, **kw):
            captured.update(kw)

    monkeypatch.setattr(asep, "Separator", Dummy)
    separator._new_separator("kim_vocal_2", "gpu", {"voice", "music"}, 256)
    assert captured["mdx_params"]["segment_size"] == 256
    assert captured["normalization_threshold"] == 1.0
    assert captured["invert_using_spec"] is False
    assert "output_single_stem" not in captured or (
        captured["output_single_stem"] is None
    )


def test_single_stem_only_writes_needed_stem(monkeypatch):
    """voice-only runs should ask the library to skip the other stems."""
    import audio_separator.separator as asep

    captured = []

    class Dummy:
        def __init__(self, **kw):
            captured.append(kw)

    monkeypatch.setattr(asep, "Separator", Dummy)
    separator._new_separator("kim_vocal_2", "cpu", {"voice"}, 256)
    assert captured[-1]["output_single_stem"] == "Vocals"
    separator._new_separator("kim_vocal_2", "cpu", {"music"}, 256)
    assert captured[-1]["output_single_stem"] == "Instrumental"
    separator._new_separator("htdemucs", "cpu", {"voice"}, 7)
    assert captured[-1]["output_single_stem"] == "Vocals"
    # demucs instrumental needs drums+bass+other → all stems written
    separator._new_separator("htdemucs", "cpu", {"music"}, 7)
    assert captured[-1].get("output_single_stem") in (None,)


def test_cpu_construction_patches_cuda_available(monkeypatch):
    """device='cpu' must build the Separator with torch.cuda.is_available()
    forced False so torch AND onnxruntime pick CPU providers."""
    import audio_separator.separator as asep

    seen = {}

    class Dummy:
        def __init__(self, **kw):
            import torch

            seen["cuda"] = torch.cuda.is_available()

    monkeypatch.setattr(asep, "Separator", Dummy)
    separator._new_separator("htdemucs", "cpu", {"voice"}, 7)
    assert seen["cuda"] is False


def test_demucs_determinism_pinned(monkeypatch):
    """R1 C3: demucs must run with ``shifts=0`` and deterministic cuDNN —
    upstream defaults to ``shifts=2`` (a ``random.randint`` shift per
    window) and cuDNN autotuning varies across processes (LF rumble)."""
    import audio_separator.separator as asep

    captured = {}

    class Dummy:
        def __init__(self, **kw):
            captured.update(kw)

    monkeypatch.setattr(asep, "Separator", Dummy)
    separator._new_separator("htdemucs", "gpu", {"voice"}, 7)
    assert captured["demucs_params"]["shifts"] == 0
    assert captured["demucs_params"]["segments_enabled"] is True
    import torch

    assert torch.backends.cudnn.deterministic is True
    assert torch.backends.cudnn.benchmark is False


# ---------- OOM ladder (Review Focus 4) --------------------------------------


def test_ladder_demucs_gpu_segment_7_4_cpu(tmp_path, monkeypatch):
    calls: list = []
    boom = {"gpu": RuntimeError("CUDA out of memory")}
    _patch_engine(monkeypatch, calls, boom)
    _write(tmp_path / "in.wav", np.random.randn(2 * SR, 2).astype(np.float32) * 0.1)
    dev = process_file(
        tmp_path / "in.wav", tmp_path / "v.wav", tmp_path / "m.wav",
        model="htdemucs", device="gpu", work_dir=tmp_path / "w",
    )
    assert dev == "cpu"
    seq = [(c["device"], c["demucs_segment"]) for c in calls]
    assert seq == [("gpu", 7), ("gpu", 4), ("cpu", 7)]


def test_ladder_mdx_gpu_straight_to_cpu(tmp_path, monkeypatch):
    """MDX must never try the broken segment-128 rung (onnx2torch)."""
    calls: list = []
    boom = {"gpu": RuntimeError("CUDA out of memory. Failed to allocate")}
    _patch_engine(monkeypatch, calls, boom)
    _write(tmp_path / "in.wav", np.random.randn(2 * SR, 2).astype(np.float32) * 0.1)
    dev = process_file(
        tmp_path / "in.wav", tmp_path / "v.wav", None,
        model="kim_vocal_2", device="gpu", work_dir=tmp_path / "w",
    )
    assert dev == "cpu"
    seq = [(c["device"], c["demucs_segment"]) for c in calls]
    assert seq == [("gpu", 256), ("cpu", 256)]


def test_ladder_non_oom_error_not_retried(tmp_path, monkeypatch):
    calls: list = []
    boom = {"gpu": ValueError("model file is corrupt")}
    _patch_engine(monkeypatch, calls, boom)
    _write(tmp_path / "in.wav", np.random.randn(SR, 2).astype(np.float32))
    with pytest.raises(ValueError, match="corrupt"):
        process_file(
            tmp_path / "in.wav", tmp_path / "v.wav", None,
            model="htdemucs", device="gpu", work_dir=tmp_path / "w",
        )
    assert len(calls) == 1


def test_ladder_cpu_oom_propagates(tmp_path, monkeypatch):
    calls: list = []
    boom = {"cpu": RuntimeError("out of memory")}
    _patch_engine(monkeypatch, calls, boom)
    _write(tmp_path / "in.wav", np.random.randn(SR, 2).astype(np.float32))
    with pytest.raises(RuntimeError, match="out of memory"):
        process_file(
            tmp_path / "in.wav", tmp_path / "v.wav", None,
            model="htdemucs", device="cpu", work_dir=tmp_path / "w",
        )
    assert len(calls) == 1


def test_failed_attempt_frees_engine(tmp_path, monkeypatch):
    calls: list = []
    engines = _patch_engine(
        monkeypatch, calls, {"gpu": RuntimeError("CUDA out of memory")}
    )
    _write(tmp_path / "in.wav", np.random.randn(SR, 2).astype(np.float32))
    process_file(
        tmp_path / "in.wav", tmp_path / "v.wav", None,
        model="htdemucs", device="gpu", work_dir=tmp_path / "w",
    )
    assert all(e.closed for e in engines)


# ---------- windowing (Review Focus 4) ----------------------------------------


def test_silent_window_skipped(tmp_path, monkeypatch):
    """An all-silent window must never reach the engine (the library can
    raise / divide by zero on pure silence) — its output stays zero."""
    calls: list = []
    engines = _patch_engine(monkeypatch, calls)
    t = np.arange(int(4 * SR)) / SR
    tone = np.stack([np.sin(2 * np.pi * 300 * t)] * 2, axis=1).astype(np.float32) * 0.3
    x = np.concatenate([tone, np.zeros((6 * SR, 2), np.float32), tone])
    _write(tmp_path / "in.wav", x)
    process_file(
        tmp_path / "in.wav", tmp_path / "v.wav", tmp_path / "m.wav",
        model="htdemucs", device="cpu", work_dir=tmp_path / "w",
        window_s=4.0, overlap_s=1.0,
    )
    # 5 spans (4 s, 1 s overlap); span idx 2 covers the all-silent 6–10 s
    assert engines[0].windows == 4, (
        f"silent window reached the engine ({engines[0].windows} calls)"
    )
    v, m = _load(tmp_path / "v.wav"), _load(tmp_path / "m.wav")
    mid = slice(int(5.0 * SR), int(9.0 * SR))
    assert np.abs(v[mid]).max() < 1e-6
    assert np.abs(m[mid]).max() < 1e-6


def test_antiphase_window_skipped(tmp_path, monkeypatch):
    """R1 C3: ``L ≈ −R`` stereo windows must never reach the engine —
    demucs normalises by ``ref.std()`` of the ~0 mono fold-down and the
    divide explodes (NaN/rumble). The window passes through as voice,
    music stays silent."""
    calls: list = []
    engines = _patch_engine(monkeypatch, calls)
    t = np.arange(int(6 * SR)) / SR
    left = 0.3 * np.sin(2 * np.pi * 220 * t)
    x = np.stack([left, -left], axis=1).astype(np.float32)
    _write(tmp_path / "in.wav", x)
    process_file(
        tmp_path / "in.wav", tmp_path / "v.wav", tmp_path / "m.wav",
        model="htdemucs", device="cpu", work_dir=tmp_path / "w",
        window_s=2.0, overlap_s=0.5,
    )
    assert engines[0].windows == 0, (
        "out-of-phase window reached the demucs engine"
    )
    v, m = _load(tmp_path / "v.wav"), _load(tmp_path / "m.wav")
    assert np.abs(v - x).max() < 1e-4
    assert np.abs(m).max() < 1e-4


def test_gain_continuity_across_windows(tmp_path, monkeypatch):
    """No level step > 1 dB across a window boundary in a steady tone."""
    _patch_engine(monkeypatch, [])
    t = np.arange(int(9 * SR)) / SR
    x = np.stack([np.sin(2 * np.pi * 400 * t)] * 2, axis=1).astype(np.float32) * 0.4
    _write(tmp_path / "in.wav", x)
    process_file(
        tmp_path / "in.wav", tmp_path / "v.wav", None,
        model="htdemucs", device="cpu", work_dir=tmp_path / "w",
        window_s=3.0, overlap_s=0.5,
    )
    y = _load(tmp_path / "v.wav").mean(axis=1)
    win = int(0.05 * SR)
    rms = np.sqrt((y[: len(y) // win * win].reshape(-1, win) ** 2).mean(axis=1))
    db = 20 * np.log10(rms + 1e-12)
    db = db[10:-10]
    assert db.max() - db.min() < 1.0


def test_both_outputs_exact_length(tmp_path, monkeypatch):
    _patch_engine(monkeypatch, [])
    x = np.random.default_rng(2).standard_normal((3 * SR, 2)).astype(np.float32) * 0.1
    _write(tmp_path / "in.wav", x)
    process_file(
        tmp_path / "in.wav", tmp_path / "v.wav", tmp_path / "m.wav",
        model="htdemucs", device="cpu", work_dir=tmp_path / "w",
    )
    for f in ("v.wav", "m.wav"):
        info = sf.info(str(tmp_path / f))
        assert info.samplerate == SR and info.frames == 3 * SR


def test_mono_input_duplicated_for_model(tmp_path, monkeypatch):
    calls: list = []
    engines = _patch_engine(monkeypatch, calls)
    x = np.random.default_rng(3).standard_normal((2 * SR, 1)).astype(np.float32) * 0.1
    _write(tmp_path / "in.wav", x)
    process_file(
        tmp_path / "in.wav", tmp_path / "v.wav", None,
        model="htdemucs", device="cpu", work_dir=tmp_path / "w",
    )
    assert engines[0].win_channels == [2]  # model always sees stereo
    info = sf.info(str(tmp_path / "v.wav"))
    assert info.channels == 1 and info.frames == 2 * SR


# ---------- real model runs ---------------------------------------------------


@pytest.fixture(scope="module")
def htdemucs_gpu_run(tmp_path_factory):
    if not GPU:
        pytest.skip("no usable GPU")
    d = tmp_path_factory.mktemp("sep_htd")
    out = {"voice": d / "voice.wav", "music": d / "music.wav"}
    with VramSampler() as vs:
        dev = process_file(
            SEP_MIX, out["voice"], out["music"],
            model="htdemucs", device="gpu", work_dir=d / "work",
        )
    out["vram_mb"] = vs.peak_mb
    out["device"] = dev
    return out


@pytest.mark.skipif(not GPU, reason="no usable GPU")
def test_htdemucs_gpu_outputs(htdemucs_gpu_run):
    assert htdemucs_gpu_run["device"] == "gpu"
    src = sf.info(str(SEP_MIX))
    for f in ("voice", "music"):
        info = sf.info(str(htdemucs_gpu_run[f]))
        assert info.samplerate == SR
        assert info.frames == src.frames


@pytest.mark.skipif(not GPU, reason="no usable GPU")
def test_htdemucs_vocals_si_sdr_gain_8db(htdemucs_gpu_run):
    mix, ref, v = _load(SEP_MIX), _load(SEP_VOICE), _load(htdemucs_gpu_run["voice"])
    gain = si_sdr_db_multi(v, ref) - si_sdr_db_multi(mix, ref)
    assert gain >= 8.0, f"vocals ΔSI-SDR {gain:.1f} dB"


@pytest.mark.skipif(not GPU, reason="no usable GPU")
def test_htdemucs_instrumental_si_sdr_gain_6db(htdemucs_gpu_run):
    mix, ref, m = _load(SEP_MIX), _load(SEP_MUSIC), _load(htdemucs_gpu_run["music"])
    gain = si_sdr_db_multi(m, ref) - si_sdr_db_multi(mix, ref)
    assert gain >= 6.0, f"instrumental ΔSI-SDR {gain:.1f} dB"


@pytest.mark.skipif(not GPU, reason="no usable GPU")
def test_htdemucs_music_only_regions_drop_15db(htdemucs_gpu_run):
    """The 1 s intro + the mid gap are music-only: the voice stem's RMS in
    those regions must be ≥15 dB below its RMS where speech is present —
    i.e. the stem goes quiet when there is no voice to keep."""
    meta = next(
        f for f in json.loads(
            (FIXTURES_DIR / "fixtures.json").read_text("utf-8")
        )["files"] if f["file"] == "sep_mix.wav"
    )
    (s1, s2) = meta["speech_intervals"]
    regions = [
        ("intro", slice(0, int(0.8 * SR))),
        ("gap", slice(int((s1[1] + 0.2) * SR), int((s2[0] - 0.2) * SR))),
    ]
    voice = _load(htdemucs_gpu_run["voice"])
    speech_level = _rms_db(
        voice[int(s1[0] * SR): int(s1[1] * SR)]
    )
    for name, region in regions:
        drop = speech_level - _rms_db(voice[region])
        assert drop >= 15.0, f"{name}: voice drop {drop:.1f} dB"


@pytest.mark.skipif(not GPU, reason="no usable GPU")
def test_htdemucs_gpu_vram_under_3gb(htdemucs_gpu_run):
    assert htdemucs_gpu_run["vram_mb"] < 3000, (
        f"VRAM delta {htdemucs_gpu_run['vram_mb']} MB"
    )


@pytest.mark.skipif(not GPU, reason="no usable GPU")
def test_htdemucs_voice_keeps_speech_words(htdemucs_gpu_run):
    """Whisper on the voice stem still hears 'quick', 'brown', 'fox'."""
    from faster_whisper import WhisperModel

    from backend.core.models_manifest import (
        model_dir,
        pinned_snapshot_dir,
    )

    snap = pinned_snapshot_dir("whisper_small")
    model = WhisperModel(
        str(snap) if snap is not None else "small",
        device="cpu", compute_type="int8",
        download_root=str(model_dir("whisper_small")),
    )
    segs, _ = model.transcribe(
        str(htdemucs_gpu_run["voice"]), beam_size=5, temperature=0.0,
        condition_on_previous_text=False, language="en",
    )
    text = " ".join(s.text for s in segs).lower()
    for word in ("quick", "brown", "fox"):
        assert word in text, f"'{word}' lost in: {text!r}"


def test_htdemucs_cpu_small_run(tmp_path):
    """CPU path works end to end (4 s slice keeps the test short)."""
    x = _load(SEP_MIX)
    n = 4 * SR
    _write(tmp_path / "in.wav", x[:n])
    dev = process_file(
        tmp_path / "in.wav", tmp_path / "v.wav", None,
        model="htdemucs", device="cpu", work_dir=tmp_path / "w",
    )
    assert dev == "cpu"
    info = sf.info(str(tmp_path / "v.wav"))
    assert info.samplerate == SR and info.frames == n
    ref = _load(SEP_VOICE)[:n]
    v = _load(tmp_path / "v.wav")
    gain = si_sdr_db_multi(v, ref) - si_sdr_db_multi(x[:n], ref)
    assert gain > 0, f"CPU vocals ΔSI-SDR {gain:.1f} dB"


@pytest.fixture(scope="module")
def mdx_gpu_run(tmp_path_factory):
    if not GPU:
        pytest.skip("no usable GPU")
    d = tmp_path_factory.mktemp("sep_mdx")
    out = {"voice": d / "voice.wav", "music": d / "music.wav"}
    with VramSampler() as vs:
        dev = process_file(
            SEP_MIX, out["voice"], out["music"],
            model="kim_vocal_2", device="gpu", work_dir=d / "work",
        )
    out["vram_mb"] = vs.peak_mb
    out["device"] = dev
    return out


@pytest.mark.skipif(not GPU, reason="no usable GPU")
def test_mdx_gpu_si_sdr_and_vram(mdx_gpu_run):
    """Kim Vocal 2 is the tighter-music-removal model: it favours a clean
    instrumental over vocal fidelity. On this fixture it measures
    ΔSI-SDR ≈ +5 dB voice / ≈ +13.6 dB instrumental — the bars assert that
    real profile (htdemucs holds the +8/+6 spec bars)."""
    assert mdx_gpu_run["device"] == "gpu"
    mix = _load(SEP_MIX)
    ref_v, ref_m = _load(SEP_VOICE), _load(SEP_MUSIC)
    v, m = _load(mdx_gpu_run["voice"]), _load(mdx_gpu_run["music"])
    dv = si_sdr_db_multi(v, ref_v) - si_sdr_db_multi(mix, ref_v)
    dm = si_sdr_db_multi(m, ref_m) - si_sdr_db_multi(mix, ref_m)
    assert dv >= 4.0, f"MDX vocals ΔSI-SDR {dv:.1f} dB"
    assert dm >= 10.0, f"MDX instrumental ΔSI-SDR {dm:.1f} dB"
    assert mdx_gpu_run["vram_mb"] < 3000, (
        f"MDX VRAM delta {mdx_gpu_run['vram_mb']} MB"
    )


def test_mdx_residual_rate_roundtrip(tmp_path):
    """1 kHz tone through the MDX residual (instrumental) path stays
    1000 ± 2 Hz; output is 48 kHz at exactly input length."""
    t = np.arange(int(4 * SR)) / SR
    speech = _load(FIXTURES_DIR / "speech.wav")
    n = 4 * SR
    sp = np.repeat(speech[:n], 2, axis=1) * 0.05  # quiet speech under a loud tone
    tone = np.stack([np.sin(2 * np.pi * 1000 * t)] * 2, axis=1) * 0.4
    _write(tmp_path / "in.wav", tone + sp[: len(tone)])
    dev = process_file(
        tmp_path / "in.wav", None, tmp_path / "m.wav",
        model="kim_vocal_2", device="cpu", work_dir=tmp_path / "w",
    )
    assert dev == "cpu"
    info = sf.info(str(tmp_path / "m.wav"))
    assert info.samplerate == SR and info.frames == n
    mono = _load(tmp_path / "m.wav").mean(axis=1)
    spec = np.abs(np.fft.rfft(mono * np.hanning(len(mono))))
    f0 = np.fft.rfftfreq(len(mono), 1 / SR)[spec.argmax()]
    assert abs(f0 - 1000) <= 2, f"residual dominant freq {f0:.1f} Hz"


def test_real_recording_voice_kept(real_dir, tmp_path):
    """Real speech-only recording: the voice stem keeps the speech and the
    music stem carries only a small residual."""
    from backend.media.decode import decode_to_wav
    from backend.media.probe import probe
    from backend.media.silence_detect import speech_intervals_vad

    rec = sorted(real_dir.glob("*.m4a"))[0]
    in_wav = tmp_path / "in.wav"
    decode_to_wav(rec, in_wav, probe(rec).audio[0].index)
    dev = "gpu" if GPU else "cpu"
    process_file(
        in_wav, tmp_path / "v.wav", tmp_path / "m.wav",
        model="htdemucs", device=dev, work_dir=tmp_path / "w",
    )
    info_in, info_v = sf.info(str(in_wav)), sf.info(str(tmp_path / "v.wav"))
    assert info_v.samplerate == SR and info_v.frames == info_in.frames
    iv_in = speech_intervals_vad(in_wav)
    iv_v = speech_intervals_vad(tmp_path / "v.wav")
    cov_in = sum(b - a for a, b in iv_in)
    cov_v = sum(b - a for a, b in iv_v)
    assert cov_v >= 0.8 * cov_in, (
        f"voice stem lost speech: VAD {cov_in:.2f}s → {cov_v:.2f}s"
    )
    # music residual is much quieter than the recording
    x, m = _load(in_wav), _load(tmp_path / "m.wav")
    assert _rms_db(m) < _rms_db(x) - 6, (
        f"music stem only {_rms_db(x) - _rms_db(m):.1f} dB below input"
    )


# ---------- R1 C3: cross-process determinism (slow) ---------------------------


_SEP_CHILD = (
    "import sys;from pathlib import Path;"
    "from backend.media.separator import process_file;"
    "process_file(Path(sys.argv[1]), Path(sys.argv[2]), None,"
    " 'htdemucs', sys.argv[3], Path(sys.argv[4]))"
)


@pytest.mark.slow
def test_demucs_cross_process_deterministic(tmp_path):
    """3 separate processes on the same input → voice stems identical
    within 1e-4 (``shifts=0`` + pinned cuDNN), and the stem must not gain
    energy in the 20–60 Hz band (the intermittent LF-rumble signature)."""
    from backend.core.capabilities import _model_present

    if not _model_present("htdemucs"):
        pytest.skip("htdemucs model not cached")
    x = _load(SEP_MIX)[: 4 * SR]
    src = tmp_path / "in.wav"
    _write(src, x)
    dev = "gpu" if GPU else "cpu"
    outs = []
    for i in range(3):
        out = tmp_path / f"v{i}.wav"
        subprocess.run(
            [
                sys.executable, "-c", _SEP_CHILD,
                str(src), str(out), dev, str(tmp_path / f"w{i}"),
            ],
            cwd=str(ROOT), check=True, timeout=900,
        )
        outs.append(out)
    ref = _load(outs[0])
    for other in outs[1:]:
        y = _load(other)
        assert y.shape == ref.shape
        drift = float(np.abs(y - ref).max())
        assert drift <= 1e-4, f"cross-process drift {drift:.2e}"

    def _lf_energy(sig: np.ndarray) -> float:
        mono = sig.mean(axis=1) if sig.ndim > 1 else sig
        spec = np.abs(np.fft.rfft(mono * np.hanning(len(mono))))
        freqs = np.fft.rfftfreq(len(mono), 1 / SR)
        return float(spec[(freqs >= 20) & (freqs <= 60)].sum())

    stem_lf, in_lf = _lf_energy(ref), _lf_energy(x)
    assert stem_lf <= in_lf, (
        f"voice stem LF energy {stem_lf:.1f} > input {in_lf:.1f} (rumble)"
    )


# ---------- resource bounds (Review Focus 5, slow) ----------------------------


class _RssSampler:
    """Peak-RSS sampler for the current process."""

    def __init__(self, interval: float = 0.5):
        self.interval = interval
        self.peak = 0
        self._stop = threading.Event()

    def _poll(self):
        import psutil

        proc = psutil.Process()
        while not self._stop.is_set():
            self.peak = max(self.peak, proc.memory_info().rss)
            self._stop.wait(self.interval)

    def __enter__(self):
        self._baseline = 0
        t = threading.Thread(target=self._poll, daemon=True)
        t.start()
        self._t = t
        return self

    def __exit__(self, *a):
        self._stop.set()
        self._t.join(timeout=5)
        import psutil

        self.peak = max(self.peak, psutil.Process().memory_info().rss)
        return False


@pytest.mark.slow
def test_20min_stereo_htdemucs_gpu_bounds(tmp_path):
    """20-min stereo htdemucs on GPU: VRAM delta < 3 GB, RSS < 3 GB."""
    if not GPU:
        pytest.skip("no usable GPU")
    n_total = 20 * 60 * SR
    src = tmp_path / "in20.wav"
    # loop the sep fixture without holding 20 min in RAM
    seg = _load(SEP_MIX)
    with sf.SoundFile(
        str(src), "w", SR, seg.shape[1], subtype="FLOAT", format="WAV"
    ) as f:
        written = 0
        while written < n_total:
            take = min(len(seg), n_total - written)
            f.write(seg[:take])
            written += take
    with VramSampler() as vs, _RssSampler() as rs:
        t0 = time.monotonic()
        process_file(
            src, tmp_path / "v.wav", None,
            model="htdemucs", device="gpu", work_dir=tmp_path / "w",
        )
        seconds = time.monotonic() - t0
    print(
        f"\n20-min htdemucs GPU: {seconds:.0f}s, "
        f"VRAM +{vs.peak_mb} MB, RSS {rs.peak / 2**30:.2f} GiB"
    )
    assert vs.peak_mb < 3000, f"VRAM delta {vs.peak_mb} MB"
    assert rs.peak < 3 * 2**30, f"RSS {rs.peak / 2**30:.2f} GiB"
