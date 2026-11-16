"""M3a Task 3 tests: device resolution, estimates, pass-through runner."""
import json
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from tests.client import make_client

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


# ---- M8 Task 0: trim inside a preview window ------------------------------------


def _long_wav(tmp_path: Path, seconds: float = 60.0) -> Path:
    """A plain 48 kHz WAV long enough for source-time trim bounds."""
    src = tmp_path / "long.wav"
    t = np.arange(int(48000 * seconds)) / 48000
    sf.write(
        str(src),
        (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32),
        48000, subtype="FLOAT",
    )
    return src


def _dur(path) -> float:
    info = sf.info(str(path))
    return info.frames / info.samplerate


def _r_frame_rate(path) -> str:
    """Raw CFR rate string (``"25/1"``); avg_frame_rate skews on short
    clips because the container duration omits the last frame's tail."""
    import subprocess

    from backend.core.ffmpeg import find_ffprobe

    cp = subprocess.run(
        [str(find_ffprobe()), "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=r_frame_rate",
         "-print_format", "csv", str(path)],
        capture_output=True, text=True, check=True,
    )
    return cp.stdout.strip().split(",")[1]


def test_preview_trim_intersecting_window(tmp_path):
    """Preview [20, 40] + trim(25, 50) keeps [25, 40) → exactly 15 s."""
    src = _long_wav(tmp_path)
    outs, _ = _run(
        src, tmp_path,
        ops=[("trim", {"start_s": 25.0, "end_s": 50.0})],
        preview=(20.0, 40.0),
    )
    assert _dur(outs[0].path) == pytest.approx(15.0, abs=0.02)


def test_preview_trim_partial_overlap(tmp_path):
    """Preview [20, 40] + trim(0, 25) keeps [20, 25) → 5 s (clipped at the
    preview edge, in preview-time coordinates)."""
    src = _long_wav(tmp_path)
    outs, _ = _run(
        src, tmp_path,
        ops=[("trim", {"start_s": 0.0, "end_s": 25.0})],
        preview=(20.0, 40.0),
    )
    assert _dur(outs[0].path) == pytest.approx(5.0, abs=0.02)


def test_preview_trim_outside_is_noop(tmp_path):
    """trim(50, 60) doesn't touch preview [20, 40] → whole preview passes
    through with a coded message instead of a ValueError."""
    src = _long_wav(tmp_path)
    outs, events = _run(
        src, tmp_path,
        ops=[("trim", {"start_s": 50.0, "end_s": 60.0})],
        preview=(20.0, 40.0),
    )
    assert _dur(outs[0].path) == pytest.approx(20.0, abs=0.02)
    assert any(e.message_code == "trim_outside_preview" for e in events)


def test_trim_full_file_still_validates(tmp_path):
    """Outside preview jobs the old contract stands: start ≥ file end is a
    human-readable error, not a silent no-op — while the same trim inside a
    preview window just no-ops."""
    src = _long_wav(tmp_path, seconds=10.0)
    p = probe(src)
    with pytest.raises(ValueError, match="Trim start must be before"):
        run_pipeline(
            media_path=src,
            probe=p,
            audio_stream_index=p.audio[0].index,
            ops=[("trim", {"start_s": 15.0})],
            output=OutputSpec(),
            job_root=tmp_path / "job",
            on_progress=lambda e: None,
            should_cancel=lambda: False,
        )
    # …and the same out-of-range trim inside a preview does not fail
    outs, _ = _run(
        src, tmp_path,
        ops=[("trim", {"start_s": 15.0})],
        preview=(2.0, 6.0),
    )
    assert _dur(outs[0].path) == pytest.approx(4.0, abs=0.02)


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
    with pytest.raises(NotImplementedError, match="bogus_op"):
        _run(CLIP, tmp_path, ops=[("bogus_op", {})])


def test_video_no_fps_falls_back_to_25(tmp_path):
    """R1 C2: a probe with no frame rate (e.g. still-image video) must
    not leave ctx.fps None — ops snap to the same 25 fps grid the
    renderer already falls back to."""
    import dataclasses

    p = probe(CLIP)
    p.video = [dataclasses.replace(p.video[0], fps=None)]
    outs = run_pipeline(
        media_path=CLIP, probe=p,
        audio_stream_index=p.audio[0].index,
        ops=[("trim", {"start_s": 0.0, "end_s": 3.33})],
        output=OutputSpec(audio_format="wav", keep_video=True),
        job_root=tmp_path / "job",
        on_progress=lambda e: None,
        should_cancel=lambda: False,
        device="cpu",
    )
    # 3.33 snapped to the 25 fps grid → 3.32
    tl = json.loads(
        (tmp_path / "job" / "output" / "timeline.json").read_text()
    )
    assert tl["segments"][0]["src_end"] == pytest.approx(3.32)
    # and the rendered video ran at the 25 fps fallback (r_frame_rate —
    # avg_frame_rate skews on short clips, see test_video_render)
    video = next(o for o in outs if o.kind == "video")
    assert _r_frame_rate(video.path) == "25/1"


def test_arabic_output_name(tmp_path):
    outs, _ = _run(ARABIC_WAV, tmp_path)
    assert "تسجيل اختبار 1" in outs[0].name


def test_sanitize_stem_windows_chars():
    stem = sanitize_stem('a<b>:"|?*.wav')
    assert not set(stem) & set('<>:"/\\|?*')
    assert sanitize_stem("تسجيل اختبار 1.wav") == "تسجيل اختبار 1"


_WIN_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def test_sanitize_stem_windows_reserved_names():
    """Windows reserves device names in *any* extension (CON.wav is
    still CON) — the stem must never produce one (R1 C1)."""
    for bad in (
        "CON.wav", "con.mp3", "NUL", "nul.flac", "aux.m4a", "PRN",
        "COM1", "com9.wav", "LPT1", "lPt9.mkv", "CON.anything.ext",
    ):
        stem = sanitize_stem(bad)
        assert stem.split(".")[0].upper() not in _WIN_RESERVED, (bad, stem)
    # names merely containing a reserved word are untouched
    assert sanitize_stem("concert.wav") == "concert"
    assert sanitize_stem("my con file.wav") == "my con file"


# ---- /api/health device fix (SPEC §9) ---------------------------------------


def _client(tmp_path):
    return make_client(
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
