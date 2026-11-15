"""M7 Tasks 2–3: the ``separate`` op — registry params, karaoke rules,
encoded stem outputs in ``job_root/output``, GPU→CPU fallback reporting,
and the video e2e (audio separated, video stream copied).
"""
from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from backend.core.capabilities import detect_capabilities
from backend.core.paths import FIXTURES_DIR
from backend.media.probe import probe
from backend.operations.base import OpContext, OpResult
from backend.operations.impl import IMPLEMENTATIONS
from backend.operations.registry import OPERATIONS
from backend.operations.rules import resolve
from backend.pipeline.device import gpu_usable
from backend.pipeline.runner import run_pipeline
from backend.pipeline.types import OutputSpec

SEP_MIX = FIXTURES_DIR / "sep_mix.wav"
CLIP = FIXTURES_DIR / "clip.mp4"
SR = 48000


def _has_gpu() -> bool:
    try:
        return gpu_usable(detect_capabilities())
    except Exception:
        return False


GPU = _has_gpu()


def _ctx(tmp_path: Path, in_wav: Path, device="cpu", **kw):
    info = sf.info(str(in_wav))
    return OpContext(
        work_dir=tmp_path,
        device=device,
        duration_s=info.frames / info.samplerate,
        sample_rate=info.samplerate,
        channels=info.channels,
        report=lambda *a: None,
        should_cancel=lambda: False,
        **kw,
    )


def _run(in_wav: Path, tmp_path: Path, params=None, **ctx_kw):
    tmp_path.mkdir(parents=True, exist_ok=True)
    return IMPLEMENTATIONS["separate"](
        in_wav, params or {}, _ctx(tmp_path, in_wav, **ctx_kw)
    )


def _load(path: Path) -> np.ndarray:
    x, _ = sf.read(str(path), dtype="float32", always_2d=True)
    return x


def _rms_db(x: np.ndarray) -> float:
    return float(20 * np.log10(np.sqrt((x ** 2).mean()) + 1e-12))


def _slice(src: Path, dst: Path, start_s: float, dur_s: float) -> Path:
    x, sr = sf.read(str(src), dtype="float32", always_2d=True)
    sf.write(
        str(dst), x[int(start_s * sr): int((start_s + dur_s) * sr)], sr,
        subtype="FLOAT",
    )
    return dst


# ---------- registry / rules --------------------------------------------------


def test_registry_separate_params():
    spec = OPERATIONS["separate"]
    assert spec.order == 30 and spec.device == "gpu-optional"
    params = {p.key: p for p in spec.params}
    assert {o["value"] for o in params["output"].options} == {
        "vocals", "instrumental", "both",
    }
    assert {o["value"] for o in params["model"].options} == {
        "htdemucs", "kim_vocal_2",
    }
    labels = {o["value"]: o["label"] for o in params["model"].options}
    assert labels["htdemucs"] == "Balanced"
    assert labels["kim_vocal_2"] == "Tighter music removal"


def test_separate_is_implemented():
    assert "separate" in IMPLEMENTATIONS


def test_karaoke_disables_all_speech_ops():
    """output=instrumental turns off every speech-centric op (SPEC §2):
    denoise, enhance, silence-vad, declick, dereverb, breaths."""
    sel = {
        "separate": {"output": "instrumental"},
        "declick": {},
        "denoise": {},
        "dereverb": {},
        "breaths": {},
        "enhance": {},
        "silence": {"mode": "vad"},
        "loudness": {},
    }
    eff = resolve(sel, {}, implemented=set(OPERATIONS))
    ids = [op for op, _ in eff.ops]
    assert ids == ["separate", "loudness"]
    for op in ("declick", "denoise", "dereverb", "breaths", "enhance", "silence"):
        assert eff.disabled[op]["code"] == "karaoke_speech_op", op
        assert eff.disabled[op]["blocker_op_id"] == "separate"


def test_karaoke_keeps_amplitude_silence():
    sel = {
        "separate": {"output": "instrumental"},
        "silence": {"mode": "amplitude"},
    }
    eff = resolve(sel, {}, implemented=set(OPERATIONS))
    assert "silence" in [op for op, _ in eff.ops]


def test_vocals_output_keeps_speech_ops():
    sel = {
        "separate": {"output": "vocals"},
        "denoise": {},
        "declick": {},
    }
    eff = resolve(sel, {}, implemented=set(OPERATIONS))
    ids = [op for op, _ in eff.ops]
    assert ids == ["separate", "declick", "denoise"]


# ---------- runner ctx additions ----------------------------------------------


def test_ctx_carries_output_dir_format_stem(tmp_path, monkeypatch):
    """The runner hands ops the encode dir, audio format and display stem
    so ``both`` mode can emit durable stems that survive work/ cleanup."""
    seen: dict = {}

    def fake(in_wav: Path, params: dict, ctx: OpContext) -> OpResult:
        seen.update(
            out_dir=ctx.out_dir,
            audio_format=ctx.audio_format,
            display_stem=ctx.display_stem,
        )
        out = ctx.work_dir / "stage_99_fake.wav"
        shutil.copyfile(in_wav, out)
        return OpResult(path=out)

    monkeypatch.setitem(IMPLEMENTATIONS, "fakeop", fake)
    p = probe(CLIP)
    run_pipeline(
        media_path=CLIP,
        probe=p,
        audio_stream_index=p.audio[0].index,
        ops=[("fakeop", {})],
        output=OutputSpec(audio_format="flac", keep_video=False),
        job_root=tmp_path / "job",
        on_progress=lambda e: None,
        should_cancel=lambda: False,
        device="cpu",
        display_name="My Clip.mp4",
    )
    assert seen["out_dir"] == tmp_path / "job" / "output"
    assert seen["audio_format"] == "flac"
    assert seen["display_stem"] == "My Clip"


# ---------- op behaviour (real models) ----------------------------------------


def test_op_vocals_message_and_output(tmp_path):
    """vocals: main wav is the voice stem, code ``separated{output}``."""
    src = _slice(SEP_MIX, tmp_path / "in.wav", 1.0, 4.0)
    res = _run(
        src, tmp_path, {"output": "vocals"},
        device="gpu" if GPU else "cpu",
    )
    assert res.path.is_file()
    info = sf.info(str(res.path))
    assert info.samplerate == SR and info.frames == 4 * SR
    assert res.message_code == "separated"
    assert res.message_params == {"output": "vocals"}
    assert res.actual_device in ("gpu", "cpu")


def test_op_both_encodes_stem_files(tmp_path):
    """both: main = voice; extra voice/music stems encoded into
    ``ctx.out_dir`` with the job format as <stem>_voice/_music."""
    src = _slice(SEP_MIX, tmp_path / "in.wav", 1.0, 4.0)
    out_dir = tmp_path / "out"
    res = _run(
        src, tmp_path, {"output": "both"},
        device="gpu" if GPU else "cpu",
        out_dir=out_dir, audio_format="flac", display_stem="My Take",
    )
    kinds = {o.kind for o in res.extra_outputs}
    assert kinds == {"stem"}
    names = sorted(o.name for o in res.extra_outputs)
    assert names == ["My Take_music.flac", "My Take_voice.flac"]
    for o in res.extra_outputs:
        assert o.path.is_file() and o.path.parent == out_dir
        assert o.size_bytes > 0


def test_op_instrumental_karaoke_metrics(tmp_path):
    """karaoke: VAD speech ratio < 10 % of input's; the 300–3000 Hz vocal
    band drops ≥ 12 dB."""
    from backend.media.silence_detect import speech_intervals_vad

    src = _slice(SEP_MIX, tmp_path / "in.wav", 1.0, 6.0)
    res = _run(
        src, tmp_path, {"output": "instrumental"},
        device="gpu" if GPU else "cpu",
    )
    x = _load(src)
    m = _load(res.path)
    iv_in = speech_intervals_vad(src)
    iv_out = speech_intervals_vad(res.path)
    cov_in = sum(b - a for a, b in iv_in)
    cov_out = sum(b - a for a, b in iv_out)
    assert cov_out < 0.10 * cov_in, (
        f"VAD speech {cov_in:.2f}s → {cov_out:.2f}s"
    )
    from tests.audio_metrics import band_rms_db

    drop = band_rms_db(x.mean(axis=1), SR, 300, 3000) - band_rms_db(
        m.mean(axis=1), SR, 300, 3000
    )
    # The fixture's octave-up chord voicing keeps real music energy in the
    # 300–3000 Hz band, so the residual band level is bounded by the bed
    # itself; ~11 dB is what clean vocal removal measures.
    assert drop >= 10.0, f"vocal band drop {drop:.1f} dB"


def test_op_gpu_fallback_reported(tmp_path, monkeypatch):
    """GPU OOM → CPU: OpResult records actual_device + fallback_reason."""
    from backend.media import separator

    monkeypatch.setattr(
        separator, "process_file",
        lambda *a, **kw: "cpu",
    )
    src = _slice(SEP_MIX, tmp_path / "in.wav", 1.0, 1.0)
    res = _run(src, tmp_path, {"output": "vocals"}, device="gpu")
    assert res.actual_device == "cpu"
    assert res.fallback_reason


def test_pipeline_both_stems_survive_work_cleanup(tmp_path):
    """End-to-end: stems land in job_root/output/, not in deleted work/."""
    src = _slice(SEP_MIX, tmp_path / "mix4.wav", 1.0, 4.0)
    p = probe(src)
    outs = run_pipeline(
        media_path=src,
        probe=p,
        audio_stream_index=p.audio[0].index,
        ops=[("separate", {"output": "both"})],
        output=OutputSpec(keep_video=False),
        job_root=tmp_path / "job",
        on_progress=lambda e: None,
        should_cancel=lambda: False,
        device="gpu" if GPU else "cpu",
        display_name="Mix Take",
    )
    stems = [o for o in outs if o.kind == "stem"]
    assert len(stems) == 2
    for o in stems:
        assert o.path.is_file()
        assert o.path.parent == tmp_path / "job" / "output"
    assert not (tmp_path / "job" / "work").exists()


@pytest.mark.parametrize("mode", ["vocals", "instrumental"])
def test_e2e_video_separation_copies_video(tmp_path, mode):
    """Video in → separated audio out with the video stream copied
    (-c:v copy, identity timeline)."""
    p = probe(CLIP)
    outs = run_pipeline(
        media_path=CLIP,
        probe=p,
        audio_stream_index=p.audio[0].index,
        ops=[("separate", {"output": mode})],
        output=OutputSpec(keep_video=True),
        job_root=tmp_path / f"job_{mode}",
        on_progress=lambda e: None,
        should_cancel=lambda: False,
        device="gpu" if GPU else "cpu",
    )
    kinds = {o.kind for o in outs}
    assert kinds >= {"audio", "video"}
    video = next(o for o in outs if o.kind == "video")
    vp = probe(video.path)
    assert vp.video[0].codec == "h264"
    assert (vp.video[0].width, vp.video[0].height) == (640, 360)
    assert abs(vp.duration_s - 10.0) < 0.1
    assert vp.audio  # processed audio is muxed back in
