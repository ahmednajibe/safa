"""M6 Task 1: the ``denoise`` op — DeepFilterNet3 ("ai") + ffmpeg afftdn
("classic").

``speech_noise.wav`` has a 0.5 s noise-only head — RMS of that head must
drop ≥10 dB (ai) / ≥6 dB (classic). Review Focus 2 (chunk seams) uses a
70 s sine+speech file; Review Focus 3 (GPU-OOM → CPU) uses a fake engine;
Review Focus 1 (30-min stereo, RAM/VRAM) is marked slow.
"""
import json
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from backend.operations.base import OpContext
from backend.operations.impl import IMPLEMENTATIONS

GENERATED = Path(__file__).parent / "fixtures" / "generated"
SPEECH_NOISE = GENERATED / "speech_noise.wav"
SPEECH = GENERATED / "speech.wav"
SR = 48000
HEAD = int(0.45 * SR)  # noise-only head of speech_noise.wav


def _dur(path: Path) -> float:
    info = sf.info(str(path))
    return info.frames / info.samplerate


def _rms_db(x: np.ndarray) -> float:
    return float(20 * np.log10(np.sqrt((x ** 2).mean()) + 1e-12))


def _ctx(tmp_path: Path, in_wav: Path, device="cpu", should_cancel=None):
    info = sf.info(str(in_wav))
    return OpContext(
        work_dir=tmp_path,
        device=device,
        duration_s=info.frames / info.samplerate,
        sample_rate=info.samplerate,
        channels=info.channels,
        report=lambda *a: None,
        should_cancel=should_cancel or (lambda: False),
    )


def _run(in_wav: Path, tmp_path: Path, params=None, **ctx_kw):
    tmp_path.mkdir(parents=True, exist_ok=True)
    return IMPLEMENTATIONS["denoise"](
        in_wav, params or {}, _ctx(tmp_path, in_wav, **ctx_kw)
    )


def _noise_head_db(path: Path) -> float:
    x, sr = sf.read(str(path), dtype="float32", always_2d=True)
    return _rms_db(x[:HEAD].mean(axis=1))


# ---- engines -----------------------------------------------------------------


def test_ai_reduces_noise_head_10db(tmp_path):
    res = _run(SPEECH_NOISE, tmp_path, {"engine": "ai"})
    before = _noise_head_db(SPEECH_NOISE)
    after = _noise_head_db(res.path)
    assert before - after >= 10.0, f"head {before:.1f} -> {after:.1f} dB"
    assert res.actual_device == "cpu"
    assert _dur(res.path) == pytest.approx(_dur(SPEECH_NOISE), abs=0.001)
    assert res.message_code == "noise_reduced"
    assert res.message_params["db"] >= 10


def test_classic_reduces_noise_head_6db(tmp_path):
    res = _run(SPEECH_NOISE, tmp_path, {"engine": "classic"})
    before = _noise_head_db(SPEECH_NOISE)
    after = _noise_head_db(res.path)
    assert before - after >= 6.0, f"head {before:.1f} -> {after:.1f} dB"
    assert res.actual_device == "cpu"
    assert _dur(res.path) == pytest.approx(_dur(SPEECH_NOISE), abs=0.001)


def test_ai_stereo_stays_stereo(tmp_path):
    res = _run(
        GENERATED / "speech_music.wav", tmp_path, {"engine": "ai"}
    )
    info = sf.info(str(res.path))
    assert info.channels == 2
    assert info.frames == sf.info(
        str(GENERATED / "speech_music.wav")
    ).frames


def test_ai_rejects_non_48k(tmp_path):
    bad = tmp_path / "bad.wav"
    sf.write(
        str(bad), np.zeros(44100, dtype=np.float32), 44100, subtype="FLOAT"
    )
    with pytest.raises((AssertionError, ValueError), match="48"):
        _run(bad, tmp_path, {"engine": "ai"})


def test_ai_cpu_never_touches_cuda(tmp_path):
    """cpu work must not initialise CUDA or allocate device memory."""
    import torch

    before = torch.cuda.is_initialized()
    mem0 = (
        torch.cuda.memory_allocated()
        if before and torch.cuda.is_available()
        else 0
    )
    _run(SPEECH_NOISE, tmp_path, {"engine": "ai"})
    assert torch.cuda.is_initialized() == before
    if before:
        assert torch.cuda.memory_allocated() == mem0


def test_cpu_job_env_hides_cuda(tmp_path):
    """SPEC §9 hard ceiling: a cpu job's worker env hides CUDA entirely."""
    import json as _json

    from backend.core.settings import Settings
    from backend.db import connect, init_db
    from backend.jobs.store import JobStore
    from backend.jobs.supervisor import Supervisor
    from backend.media.probe import probe

    db = tmp_path / "app.db"
    init_db(db)
    con = connect(db)
    try:
        con.execute(
            "INSERT INTO media (id, filename, stored_path, size_bytes,"
            " probe_json, created_at) VALUES (?,?,?,?,?,?)",
            (
                "m1", SPEECH.name, str(SPEECH), SPEECH.stat().st_size,
                _json.dumps(probe(SPEECH).to_dict()),
                "2026-11-15T00:00:00",
            ),
        )
        con.commit()
    finally:
        con.close()
    store = JobStore(db)
    job = store.create(
        "m1", "full", [], {"audio_format": "wav"}, None, "cpu", 0
    )
    env = Supervisor(
        store, Settings(data_dir=tmp_path, db_path=db)
    ).worker_env(job)
    assert env["CUDA_VISIBLE_DEVICES"] == ""


# ---- Review Focus 3: GPU OOM -> CPU retry ----------------------------------------


def _fake_oom_engine(monkeypatch):
    """Fake DFN engine: raises OOM on gpu, attenuates on cpu."""
    import torch

    import backend.media.dfn as dfn

    class _Fake:
        def __init__(self, device):
            self.device = device
            self.calls = 0

        def process(self, x, atten_lim_db):
            self.calls += 1
            if self.device == "gpu":
                raise torch.cuda.OutOfMemoryError("fake oom")
            return np.asarray(x, dtype=np.float32) * 0.5

        def close(self):
            pass

    made = []

    def _get(device):
        eng = _Fake(device)
        made.append(eng)
        return eng

    monkeypatch.setattr(dfn, "get_engine", _get)
    return made


def test_process_file_falls_back_to_cpu_on_oom(tmp_path, monkeypatch):
    import backend.media.dfn as dfn

    made = _fake_oom_engine(monkeypatch)
    out = tmp_path / "out.wav"
    actual = dfn.process_file(
        SPEECH_NOISE, out, 24.0, "gpu", None, lambda: False
    )
    assert actual == "cpu"
    assert made and made[0].device == "gpu"  # tried gpu first
    assert any(e.device == "cpu" for e in made)
    x, _ = sf.read(str(SPEECH_NOISE), dtype="float32")
    y, _ = sf.read(str(out), dtype="float32")
    assert len(y) == len(x)
    assert np.allclose(y, x * 0.5)


def test_denoise_op_reports_cpu_fallback(tmp_path, monkeypatch):
    _fake_oom_engine(monkeypatch)
    res = _run(SPEECH_NOISE, tmp_path, {"engine": "ai"}, device="gpu")
    assert res.actual_device == "cpu"
    assert res.fallback_reason


def test_denoise_worker_stage_record_on_oom(tmp_path, monkeypatch):
    """The persisted stage record shows the cpu fallback + warning."""
    _fake_oom_engine(monkeypatch)
    # The stage must be planned on gpu for the OOM path to run at all —
    # on a GPU-less box (CI) resolve_device would pick cpu up front.
    import backend.pipeline.device as device_mod
    monkeypatch.setattr(device_mod, "gpu_usable", lambda caps: True)
    from backend.db import connect, init_db
    from backend.jobs.store import JobStore
    from backend.media.probe import probe
    from backend.worker import main as worker_main

    db = tmp_path / "app.db"
    data = tmp_path / "data"
    data.mkdir()
    init_db(db)
    monkeypatch.setenv("SOUND_SYSTEM_DB", str(db))
    monkeypatch.setenv("SOUND_SYSTEM_DATA", str(data))
    con = connect(db)
    try:
        con.execute(
            "INSERT INTO media (id, filename, stored_path, size_bytes,"
            " probe_json, created_at) VALUES (?,?,?,?,?,?)",
            (
                "m1", SPEECH_NOISE.name, str(SPEECH_NOISE),
                SPEECH_NOISE.stat().st_size,
                json.dumps(probe(SPEECH_NOISE).to_dict()),
                "2026-11-15T00:00:00",
            ),
        )
        con.commit()
    finally:
        con.close()
    store = JobStore(db)
    job = store.create(
        "m1", "full", [("denoise", {"engine": "ai"})],
        {"audio_format": "wav", "keep_video": False}, None, "gpu", 0,
    )
    assert worker_main([job.id]) == 0
    got = store.get(job.id)
    assert got.state == "completed"
    s = got.stages[0]
    assert s["state"] == "done"
    assert s["actual_device"] == "cpu"
    assert s["fallback_reason"]
    assert s["warning_code"] == "gpu_fallback"
    assert any(
        w["warning_code"] == "gpu_fallback" for w in got.warnings
    )


# ---- Review Focus 2: chunk seams -------------------------------------------------


def _sine_speech(path: Path, seconds: float) -> Path:
    """440 Hz sine under looped speech — continuous content across every
    30 s chunk boundary."""
    x, sr = sf.read(str(SPEECH), dtype="float32")
    n = int(seconds * SR)
    loop = np.resize(x, n) * 0.5
    t = np.arange(n) / SR
    loop += 0.15 * np.sin(2 * np.pi * 440.0 * t).astype(np.float32)
    sf.write(str(path), loop.astype(np.float32), SR, subtype="FLOAT")
    return path


def _max_jump(x: np.ndarray, centres: list[int], win: int):
    """(max |Δx| within ±win of each centre, max |Δx| outside them)."""
    d = np.abs(np.diff(x))
    mask = np.zeros(len(d), bool)
    for c in centres:
        mask[max(0, c - win):c + win] = True
    inside = d[mask].max() if mask.any() else 0.0
    outside = d[~mask].max() if (~mask).any() else 0.0
    return float(inside), float(outside)


def test_chunk_seams_bounded(tmp_path):
    """70 s file → 3 DFN chunks; sample jumps at seams ≤ 1.5× elsewhere."""
    src = _sine_speech(tmp_path / "long70.wav", 70.0)
    res = _run(src, tmp_path, {"engine": "ai"})
    y, sr = sf.read(str(res.path), dtype="float32", always_2d=True)
    y = y.mean(axis=1)
    n = len(y)
    chunk, hop = int(30 * sr), int(29 * sr)
    seams = [a for a in range(hop, n, hop)]
    inside, outside = _max_jump(y, seams, int(0.005 * sr))
    assert inside <= 1.5 * outside, (
        f"seam jump {inside:.5f} > 1.5x elsewhere {outside:.5f}"
    )


def test_ola_identity_is_seamless(tmp_path):
    """process_ola with an identity fn must reproduce the input exactly."""
    from backend.media.chunks import process_ola

    src = _sine_speech(tmp_path / "sine.wav", 70.0)
    out = tmp_path / "ola.wav"
    process_ola(
        src, out, 30 * SR, SR,
        lambda block: block, None, None,
    )
    x, _ = sf.read(str(src), dtype="float32")
    y, _ = sf.read(str(out), dtype="float32")
    assert len(y) == len(x)
    np.testing.assert_allclose(y, x, atol=1e-4)


# ---- real recordings --------------------------------------------------------------


def test_recording3_nonspeech_drops_speech_kept(tmp_path, real_dir):
    """Recording (3): non-speech level −≥2.5 dB at matched loudness and
    ≥95 % of VAD speech retained (Task 1 tests)."""
    import subprocess

    from backend.core.ffmpeg import find_ffmpeg
    from backend.media.decode import decode_to_wav
    from backend.media.silence_detect import (
        non_speech_dbfs,
        speech_intervals_vad,
    )

    src = real_dir / "Recording (3).m4a"
    in_wav = tmp_path / "in.wav"
    decode_to_wav(src, in_wav, 0)
    res = _run(in_wav, tmp_path, {"engine": "ai"})

    # matched loudness before comparing floors
    ff = str(find_ffmpeg())
    normed = []
    for i, w in enumerate((in_wav, res.path)):
        dst = tmp_path / f"norm{i}.wav"
        subprocess.run(
            [ff, "-v", "error", "-y", "-i", str(w),
             "-af", "loudnorm=I=-16:TP=-1:LRA=11",
             "-ar", "48000", "-c:a", "pcm_f32le", str(dst)],
            check=True,
        )
        normed.append(dst)
    drop = non_speech_dbfs(normed[0]) - non_speech_dbfs(normed[1])
    assert drop >= 2.5, f"non-speech drop {drop:.2f} dB < 2.5"

    in_speech = sum(b - a for a, b in speech_intervals_vad(in_wav))
    out_speech = sum(b - a for a, b in speech_intervals_vad(res.path))
    assert out_speech >= 0.95 * in_speech, (
        f"speech {out_speech:.2f}s < 95% of {in_speech:.2f}s"
    )


# ---- Review Focus 1 (slow): 30-min stereo, bounded RAM/VRAM -------------------------


def _make_stereo_long(path: Path, target_s: float = 1800.0) -> None:
    """30-min stereo: speech loop + quiet stereo noise."""
    x, sr = sf.read(str(SPEECH), dtype="float32")
    rng = np.random.default_rng(0)
    written = 0
    with sf.SoundFile(
        str(path), "w", samplerate=SR, channels=2,
        format="WAV", subtype="FLOAT",
    ) as f:
        while written < int(target_s * SR):
            stereo = np.stack([x, np.roll(x, 800)], axis=1) * 0.7
            stereo += 0.02 * rng.standard_normal(stereo.shape).astype(
                np.float32
            )
            f.write(stereo.astype(np.float32))
            written += len(stereo)


@pytest.mark.slow
@pytest.mark.parametrize("device", ["cpu", "gpu"])
def test_denoise_ai_30min_stereo_bounds(tmp_path, device):
    """30-min stereo through denoise(ai): completes, peak RSS < 2 GB,
    peak VRAM delta < 1 GB (gpu), exact output length."""
    import os
    import threading

    import psutil

    import backend.media.dfn as dfn
    from backend.core.gpu import VramSampler

    if device == "gpu":
        import torch

        if not torch.cuda.is_available():
            pytest.skip("no CUDA device")

    long_wav = tmp_path / "long30.wav"
    _make_stereo_long(long_wav)
    out = tmp_path / "out.wav"

    me = psutil.Process(os.getpid())
    stop = threading.Event()
    holder: dict = {}

    def _poll():
        peak = me.memory_info().rss
        while not stop.is_set():
            try:
                peak = max(peak, me.memory_info().rss)
            except psutil.NoSuchProcess:
                break
            stop.wait(0.5)
        holder["peak"] = peak

    t = threading.Thread(target=_poll, daemon=True)
    t.start()
    with VramSampler() as vs:
        actual = dfn.process_file(
            long_wav, out, 24.0, device, None, lambda: False
        )
    stop.set()
    t.join(timeout=5)

    assert actual == device or (device == "gpu" and actual == "cpu")
    ix, iy = sf.info(str(long_wav)), sf.info(str(out))
    assert iy.frames == ix.frames and iy.channels == 2
    print(
        f"\n[{device}] rss_peak={holder['peak'] / 2**30:.2f} GiB "
        f"vram_delta={vs.peak_mb} MB"
    )
    assert holder["peak"] < 2 * (1 << 30)
    if device == "gpu" and actual == "gpu":
        assert vs.peak_mb < 1024
