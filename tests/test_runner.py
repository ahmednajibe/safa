"""M3a Task 3 tests: device resolution, estimates, pass-through runner."""
import json
from pathlib import Path

import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from backend.core.capabilities import Capabilities
from backend.core.settings import Settings
from backend.db import connect
from backend.main import create_app
from backend.media.probe import probe
from backend.media.storage import sanitize_stem
from backend.pipeline.device import resolve_device
from backend.pipeline.estimates import bucket_for, estimate_seconds
from backend.pipeline.runner import run_pipeline
from backend.pipeline.types import (
    CancelledError,
    OutputSpec,
)

GENERATED = Path(__file__).parent / "fixtures" / "generated"
CLIP = GENERATED / "clip.mp4"
ARABIC_WAV = GENERATED / "تسجيل اختبار 1.wav"

_ALL_MODULES = {
    "torch": True, "df": True, "audio_separator": True,
    "silero_vad": True, "pedalboard": True, "faster_whisper": True,
    "onnxruntime": True,
}


def _caps(gpu=True, cuda_dlls=True, modules=None):
    return Capabilities(
        python="3.11",
        gpu={"name": "GTX 1650", "total_mb": 4096} if gpu else None,
        cuda_dlls=cuda_dlls,
        ffmpeg={},
        modules=dict(_ALL_MODULES if modules is None else modules),
        models={},
    )


# ---- resolve_device (SPEC §9) ----------------------------------------------


def test_resolve_device_matrix():
    caps = _caps()
    # job cpu is a hard ceiling
    assert resolve_device("cpu", "separate", {}, caps) == "cpu"
    assert resolve_device("cpu", "subtitles", {}, caps) == "cpu"
    # gpu job: only capable engines use the GPU
    assert resolve_device("gpu", "separate", {"model": "htdemucs"}, caps) == "gpu"
    assert (
        resolve_device("gpu", "separate", {"model": "kim_vocal_2"}, caps)
        == "gpu"
    )
    assert resolve_device("gpu", "denoise", {"engine": "ai"}, caps) == "gpu"
    assert resolve_device("gpu", "denoise", {"engine": "classic"}, caps) == "cpu"
    assert resolve_device("gpu", "subtitles", {}, caps) == "gpu"
    # everything else always cpu
    for op in ("loudness", "trim", "enhance", "silence", "channel"):
        assert resolve_device("gpu", op, {}, caps) == "cpu"


def test_resolve_device_no_gpu_caps():
    caps = _caps(gpu=False)
    for op in ("separate", "denoise", "subtitles"):
        assert resolve_device("gpu", op, {"engine": "ai"}, caps) == "cpu"


def test_resolve_device_missing_cuda_dlls_or_module():
    no_dlls = _caps(cuda_dlls=False)
    assert (
        resolve_device("gpu", "separate", {"model": "htdemucs"}, no_dlls)
        == "cpu"
    )
    no_ort = _caps(modules={**_ALL_MODULES, "onnxruntime": False})
    assert (
        resolve_device("gpu", "separate", {"model": "kim_vocal_2"}, no_ort)
        == "cpu"
    )
    no_df = _caps(modules={**_ALL_MODULES, "df": False})
    assert resolve_device("gpu", "denoise", {"engine": "ai"}, no_df) == "cpu"


# ---- estimates --------------------------------------------------------------


def test_estimate_buckets():
    ops = [("separate", {"model": "htdemucs"})]
    gpu = estimate_seconds(600.0, ops, "gpu", caps=_caps())
    cpu = estimate_seconds(600.0, ops, "cpu", caps=_caps())
    assert cpu[0] > gpu[0]
    for low, high in (gpu, cpu):
        assert 0 < low <= high
    assert bucket_for(30) == "Under a minute"
    assert bucket_for(150) == "About 3 min"
    assert bucket_for(700) == "About 12 min"
    assert bucket_for(2000) == "Over 30 min"


def test_estimate_no_ops_is_small():
    low, high = estimate_seconds(60.0, [], "cpu", caps=_caps())
    assert low <= high < 60


# ---- runner pass-through ----------------------------------------------------


def _run(media_path, tmp_path, ops=None, output=None, preview=None,
         device="cpu", cancel=None, stream=None):
    p = probe(media_path)
    events = []
    outs = run_pipeline(
        media_path=media_path,
        probe=p,
        audio_stream_index=(p.audio[0].index if stream is None else stream),
        ops=ops or [],
        output=output or OutputSpec(),
        job_root=tmp_path / "job",
        on_progress=events.append,
        should_cancel=cancel or (lambda: False),
        preview=preview,
        device=device,
    )
    return outs, events


def test_passthrough_audio_real_m4a(real_dir, tmp_path):
    src = real_dir / "Recording (2).m4a"
    outs, _ = _run(src, tmp_path)
    assert len(outs) == 1
    assert outs[0].kind == "audio"
    assert outs[0].name.endswith(".wav")
    info = sf.info(outs[0].path)
    src_probe = probe(src)
    assert abs(info.frames / info.samplerate - src_probe.duration_s) < 0.02


def test_passthrough_video(tmp_path):
    outs, _ = _run(CLIP, tmp_path)
    kinds = {o.kind for o in outs}
    assert kinds == {"audio", "video"}
    video = next(o for o in outs if o.kind == "video")
    p = probe(video.path)
    assert p.video[0].codec == "h264"
    assert (p.video[0].width, p.video[0].height) == (640, 360)
    assert p.audio[0].codec == "aac"
    assert abs(p.duration_s - 10.0) < 0.05


def test_passthrough_no_video_when_keep_video_false(tmp_path):
    outs, _ = _run(CLIP, tmp_path, output=OutputSpec(keep_video=False))
    assert {o.kind for o in outs} == {"audio"}


def test_preview_range_audio_only(tmp_path):
    outs, _ = _run(CLIP, tmp_path, preview=(1.0, 4.0))
    assert len(outs) == 1
    assert outs[0].kind == "audio"
    info = sf.info(outs[0].path)
    assert abs(info.frames / info.samplerate - 3.0) < 0.03


def test_progress_called_in_order(tmp_path):
    _, events = _run(CLIP, tmp_path)
    stages = [e.stage for e in events]
    assert stages[:3] == ["Reading file", "Saving audio", "Rebuilding video"]
    idxs = [e.stage_idx for e in events[:3]]
    assert idxs == sorted(idxs)
    assert all(e.total_stages == 3 for e in events[:3])
    assert events[-1].overall_pct == 1.0


def test_progress_audio_only_stages(tmp_path):
    _, events = _run(
        ARABIC_WAV, tmp_path,
        output=OutputSpec(audio_format="flac"),
    )
    assert [e.stage for e in events[:2]] == ["Reading file", "Saving audio"]


def test_cancel_between_stages(tmp_path):
    calls = iter(range(100))

    def cancel():
        return next(calls) >= 1  # True from the second check on

    with pytest.raises(CancelledError):
        _run(CLIP, tmp_path, cancel=cancel)
    outdir = tmp_path / "job" / "output"
    assert not outdir.exists() or not any(outdir.iterdir())


def test_unknown_op_raises(tmp_path):
    with pytest.raises(NotImplementedError, match="denoise"):
        _run(CLIP, tmp_path, ops=[("denoise", {})])


def test_arabic_output_name(tmp_path):
    outs, _ = _run(ARABIC_WAV, tmp_path)
    assert "تسجيل اختبار 1" in outs[0].name


def test_sanitize_stem_windows_chars():
    stem = sanitize_stem('a<b>:"|?*.wav')
    assert not set(stem) & set('<>:"/\\|?*')
    assert sanitize_stem("تسجيل اختبار 1.wav") == "تسجيل اختبار 1"


# ---- /api/health device fix (SPEC §9) ---------------------------------------


def _client(tmp_path):
    return TestClient(
        create_app(Settings(data_dir=tmp_path, db_path=tmp_path / "app.db"))
    )


def test_health_device_requires_gpu_dlls_torch(tmp_path, monkeypatch):
    import backend.api.health as health_mod

    monkeypatch.setattr(
        health_mod, "detect_capabilities",
        lambda: _caps(gpu=True, cuda_dlls=False),
    )
    r = _client(tmp_path).get("/api/health")
    assert r.json()["device"] == "cpu"

    monkeypatch.setattr(
        health_mod, "detect_capabilities",
        lambda: _caps(modules={**_ALL_MODULES, "torch": False}),
    )
    assert _client(tmp_path).get("/api/health").json()["device"] == "cpu"

    monkeypatch.setattr(
        health_mod, "detect_capabilities", lambda: _caps()
    )
    assert _client(tmp_path).get("/api/health").json()["device"] == "gpu"


# ---- /api/estimate ----------------------------------------------------------


def _insert_media(db_path: Path, media_id: str, duration_s: float):
    con = connect(db_path)
    try:
        con.execute(
            "INSERT INTO media (id, filename, stored_path, size_bytes,"
            " probe_json, created_at) VALUES (?,?,?,?,?,?)",
            (
                media_id, "clip.mp4", "/x/original.mp4", 1000,
                json.dumps({"duration_s": duration_s, "has_video": True,
                            "audio": [], "video": []}),
                "2026-11-15T00:00:00",
            ),
        )
        con.commit()
    finally:
        con.close()


def test_api_estimate(tmp_path):
    db = tmp_path / "app.db"
    c = _client(tmp_path)
    _insert_media(db, "m1", 600.0)
    r = c.get(
        "/api/estimate",
        params={
            "media_id": "m1",
            "device": "cpu",
            "ops": json.dumps([{"id": "separate", "params": {}}]),
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["low_s"] <= body["high_s"]
    assert body["bucket"]


def test_api_estimate_default_device_and_404(tmp_path):
    c = _client(tmp_path)
    _insert_media(tmp_path / "app.db", "m2", 30.0)
    assert c.get(
        "/api/estimate", params={"media_id": "m2", "ops": "[]"}
    ).status_code == 200
    assert c.get(
        "/api/estimate", params={"media_id": "nope", "ops": "[]"}
    ).status_code == 404
    assert c.get(
        "/api/estimate", params={"media_id": "m2", "ops": "not json"}
    ).status_code == 422
