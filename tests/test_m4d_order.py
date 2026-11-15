"""M4d Task 1: processing order, hidden ``declick_final``, carried
analysis, VAD-gated enhance calibration, breath-span reuse.

The pipeline order moves ``silence`` ahead of ``enhance`` and adds a
hidden second declick pass right after enhance (plan Task 1). A shared
``ctx.analysis`` dict carries pre-cut measurements (noise floor, speech
level, breath spans) so later stages calibrate on what the *input*
sounded like, not on what's left after the silence cut.
"""
import json
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from backend.core.settings import Settings
from backend.main import create_app
from backend.media.probe import probe
from backend.operations.base import OpContext, OpResult
from backend.operations.impl import IMPLEMENTATIONS
from backend.operations.registry import OPERATIONS, ops_in_order
from backend.operations.rules import resolve
from backend.pipeline.runner import run_pipeline
from backend.pipeline.types import OutputSpec

GENERATED = Path(__file__).parent / "fixtures" / "generated"
SPEECH_SIL = GENERATED / "speech_silences.wav"
SPEECH = GENERATED / "speech.wav"
SR = 48000


def _ctx(tmp_path: Path, in_wav: Path, **kw) -> OpContext:
    info = sf.info(str(in_wav))
    tmp_path.mkdir(parents=True, exist_ok=True)
    return OpContext(
        work_dir=tmp_path,
        device="cpu",
        duration_s=info.frames / info.samplerate,
        sample_rate=info.samplerate,
        channels=info.channels,
        report=kw.pop("report", lambda *a: None),
        should_cancel=kw.pop("should_cancel", lambda: False),
        **kw,
    )


# ---- order + hidden op ---------------------------------------------------


def test_m4d_op_order():
    """Plan Task 1 target order; hidden declick_final sits at 52."""
    assert OPERATIONS["trim"].order == 10
    assert OPERATIONS["channel"].order == 15
    assert OPERATIONS["highpass"].order == 20
    assert OPERATIONS["dehum"].order == 25
    assert OPERATIONS["separate"].order == 30
    assert OPERATIONS["declick"].order == 38
    assert OPERATIONS["denoise"].order == 40
    assert OPERATIONS["dereverb"].order == 42
    assert OPERATIONS["breaths"].order == 46
    assert OPERATIONS["silence"].order == 48
    assert OPERATIONS["enhance"].order == 50
    assert OPERATIONS["declick_final"].order == 52
    assert OPERATIONS["enhance_ai"].order == 55
    assert OPERATIONS["speed"].order == 70
    assert OPERATIONS["loudness"].order == 80
    assert OPERATIONS["subtitles"].order == 90


def test_declick_final_is_hidden():
    assert OPERATIONS["declick_final"].hidden is True
    for op_id, spec in OPERATIONS.items():
        if op_id != "declick_final":
            assert spec.hidden is False, op_id


def test_operations_endpoint_hides_declick_final(tmp_path):
    from fastapi.testclient import TestClient

    c = TestClient(
        create_app(Settings(data_dir=tmp_path, db_path=tmp_path / "app.db"))
    )
    ops = c.get("/api/operations").json()["operations"]
    ids = [o["id"] for o in ops]
    assert "declick_final" not in ids
    assert "declick" in ids


# ---- resolve() auto-insert -----------------------------------------------


def _resolve(ops):
    return resolve(
        ops,
        {op_id: True for op_id in OPERATIONS},
        set(OPERATIONS),
    )


def test_resolve_inserts_declick_final_after_enhance():
    eff = _resolve({"declick": {}, "enhance": {}, "loudness": {}})
    ids = [op_id for op_id, _ in eff.ops]
    assert ids == ["declick", "enhance", "declick_final", "loudness"]


def test_resolve_no_final_without_declick():
    eff = _resolve({"enhance": {}, "loudness": {}})
    ids = [op_id for op_id, _ in eff.ops]
    assert "declick_final" not in ids


def test_resolve_no_final_without_enhance():
    """The residual pass tails *enhance* — declick alone doesn't get one."""
    eff = _resolve({"declick": {}, "denoise": {}})
    ids = [op_id for op_id, _ in eff.ops]
    assert "declick_final" not in ids


def test_resolve_strips_stray_declick_final():
    """A client-sent declick_final without declick must not run."""
    eff = _resolve({"declick_final": {}, "loudness": {}})
    ids = [op_id for op_id, _ in eff.ops]
    assert "declick_final" not in ids


def test_resolve_declick_final_idempotent():
    """Frontend POSTs back the effective list — resolve() must not
    duplicate or move the hidden stage."""
    first = _resolve({"declick": {}, "enhance": {}})
    again = _resolve(dict(first.ops))
    assert [op_id for op_id, _ in again.ops] == [
        op_id for op_id, _ in first.ops
    ]
    ids = [op_id for op_id, _ in again.ops]
    assert ids.count("declick_final") == 1
    assert ids == ["declick", "enhance", "declick_final"]


def test_podcast_preset_effective_order():
    """The full podcast preset resolves into the M4d order incl. the
    hidden final pass."""
    from backend.operations.registry import PRESETS

    eff = _resolve(dict(PRESETS["podcast"]["ops"]))
    ids = [op_id for op_id, _ in eff.ops]
    assert ids == [
        "highpass", "dehum", "declick", "denoise", "dereverb",
        "breaths", "silence", "enhance", "declick_final", "loudness",
    ]


# ---- carried analysis -----------------------------------------------------


def test_context_has_analysis_dict(tmp_path):
    ctx = _ctx(tmp_path, SPEECH)
    assert isinstance(ctx.analysis, dict)


def test_runner_threads_analysis_between_stages(tmp_path, monkeypatch):
    """Stage A writes analysis keys; stage B sees them (plan Task 1 —
    the runner must share one dict across all per-stage contexts)."""
    seen: dict = {}

    def _writer(in_wav, params, ctx):
        ctx.analysis["from_a"] = {"v": 1}
        return OpResult(path=in_wav)

    def _reader(in_wav, params, ctx):
        seen.update(ctx.analysis)
        return OpResult(path=in_wav)

    monkeypatch.setitem(IMPLEMENTATIONS, "fake_a", _writer)
    monkeypatch.setitem(IMPLEMENTATIONS, "fake_b", _reader)

    p = probe(SPEECH)
    run_pipeline(
        media_path=SPEECH,
        probe=p,
        audio_stream_index=p.audio[0].index,
        ops=[("fake_a", {}), ("fake_b", {})],
        output=OutputSpec(),
        job_root=tmp_path / "job",
        on_progress=lambda e: None,
        should_cancel=lambda: False,
        device="cpu",
    )
    assert seen.get("from_a") == {"v": 1}


def test_denoise_writes_vad_analysis(tmp_path, real_dir):
    """The first VAD stage records the pre-cut floor + speech level
    (plan Task 1: analysis keys ``noise_floor_dbfs``/``speech_dbfs``)."""
    from backend.media.decode import decode_to_wav

    in_wav = tmp_path / "in.wav"
    decode_to_wav(real_dir / "Recording (2).m4a", in_wav, 0)
    ctx = _ctx(tmp_path / "work", in_wav)
    IMPLEMENTATIONS["denoise"](in_wav, {"engine": "classic"}, ctx)
    assert "noise_floor_dbfs" in ctx.analysis
    assert "speech_dbfs" in ctx.analysis
    assert -90.0 <= ctx.analysis["noise_floor_dbfs"] <= -20.0
    assert ctx.analysis["speech_dbfs"] > ctx.analysis["noise_floor_dbfs"]


def test_breaths_writes_spans_for_silence(tmp_path):
    """breaths stores its detected spans (in its input's time) so the
    silence stage can reuse them after any mapping (plan Task 1)."""
    ctx = _ctx(tmp_path, SPEECH_SIL)
    IMPLEMENTATIONS["breaths"](
        SPEECH_SIL, {"mode": "duck", "sensitivity": "high"}, ctx
    )
    assert "breath_spans" in ctx.analysis
    assert "breath_spans_dur" in ctx.analysis
    for s, e in ctx.analysis["breath_spans"]:
        assert 0.0 <= s < e <= ctx.analysis["breath_spans_dur"] + 1e-6


def _first_vad_gap(wav: Path, min_gap: float = 0.6):
    """(speech_end, next_speech_start) of the first VAD gap ≥ min_gap."""
    from backend.media.silence_detect import speech_intervals_vad

    speech = speech_intervals_vad(wav)
    for (_, b0), (a1, _) in zip(speech, speech[1:]):
        if a1 - b0 >= min_gap:
            return b0, a1
    raise AssertionError("fixture has no cuttable gap")


def test_silence_reuses_carried_breath_spans(tmp_path, monkeypatch):
    """When breaths ran earlier, silence must NOT re-detect after
    ducking — it consumes ``analysis['breath_spans']`` (plan Task 1)."""
    import backend.media.breaths as breaths_mod

    def _boom(*a, **k):
        raise AssertionError("silence re-detected breaths")

    monkeypatch.setattr(breaths_mod, "detect_breaths", _boom)

    src = tmp_path / "in.wav"
    import shutil

    shutil.copyfile(SPEECH_SIL, src)
    dur = sf.info(str(src)).frames / SR
    b0, _a1 = _first_vad_gap(src)

    # A breath reported just inside the cut gap sits inside the pad
    # zone — the keep edge must clamp at its start (plan Task 1).
    breath_start = b0 + 0.05
    ctx = _ctx(
        tmp_path / "work", src,
        analysis={
            "breath_spans": [(breath_start, breath_start + 0.25)],
            "breath_spans_dur": dur,
        },
    )
    res = IMPLEMENTATIONS["silence"](
        src, {"mode": "vad", "pad_s": 0.4}, ctx,
    )
    kept = res.timeline.kept_spans_src()
    first_end = next(e for _s, e in kept if e > b0 - 0.01)
    assert first_end == pytest.approx(breath_start, abs=0.03)


def test_silence_ignores_stale_breath_spans(tmp_path, monkeypatch):
    """Carried spans from a *different-length* stage input are stale —
    silence falls back to its own detection (no bogus clamps)."""
    import backend.media.breaths as breaths_mod

    called = {"n": 0}
    orig = breaths_mod.detect_breaths

    def _spy(*a, **k):
        called["n"] += 1
        return orig(*a, **k)

    monkeypatch.setattr(breaths_mod, "detect_breaths", _spy)

    src = tmp_path / "in.wav"
    import shutil

    shutil.copyfile(SPEECH_SIL, src)
    b0, _a1 = _first_vad_gap(src)
    breath_start = b0 + 0.05
    ctx = _ctx(
        tmp_path / "work", src,
        analysis={
            # same span as the reuse test, but a stale duration → must
            # be ignored (would clamp the pad at breath_start otherwise)
            "breath_spans": [(breath_start, breath_start + 0.25)],
            "breath_spans_dur": 999.0,
        },
    )
    res = IMPLEMENTATIONS["silence"](
        src, {"mode": "vad", "pad_s": 0.4}, ctx,
    )
    kept = res.timeline.kept_spans_src()
    first_end = next(e for _s, e in kept if e > b0 - 0.01)
    # stale spans ignored → pad keeps its full 0.4 s (or a real
    # detected breath's clamp — never the fabricated 0.05 s point)
    assert abs(first_end - breath_start) > 0.03
    assert called["n"] >= 1  # fell back to real detection


# ---- enhance calibration ----------------------------------------------------


def test_enhance_uses_carried_analysis(tmp_path):
    """Carried floor/speech values must drive the calibration (plan
    Task 1) — the same file enhanced under two different carried
    analyses comes out measurably different."""
    res_a = IMPLEMENTATIONS["enhance"](
        SPEECH_SIL, {}, _ctx(
            tmp_path / "a", SPEECH_SIL,
            analysis={"noise_floor_dbfs": -30.0, "speech_dbfs": -45.0},
        ),
    )
    res_b = IMPLEMENTATIONS["enhance"](
        SPEECH_SIL, {}, _ctx(
            tmp_path / "b", SPEECH_SIL,
            analysis={"noise_floor_dbfs": -70.0, "speech_dbfs": -10.0},
        ),
    )
    ya, _ = sf.read(str(res_a.path))
    yb, _ = sf.read(str(res_b.path))
    rms_a = float(np.sqrt((np.asarray(ya) ** 2).mean()))
    rms_b = float(np.sqrt((np.asarray(yb) ** 2).mean()))
    assert abs(20 * np.log10(rms_a / rms_b)) > 1.0


def test_enhance_vad_gated_fallback(tmp_path, real_dir):
    """Without carried values enhance computes *VAD-gated* stats
    (floor on non-speech, speech on speech) — equal to what an explicit
    identical analysis would produce, and different from all-frame
    percentiles on a speech+silence file (plan Task 1)."""
    from backend.media.decode import decode_to_wav
    from backend.media.vad_stats import vad_level_stats

    in_wav = tmp_path / "in.wav"
    decode_to_wav(real_dir / "Recording (2).m4a", in_wav, 0)
    stats = vad_level_stats(in_wav)
    assert set(stats) >= {"noise_floor_dbfs", "speech_dbfs"}

    res_fresh = IMPLEMENTATIONS["enhance"](
        in_wav, {}, _ctx(tmp_path / "fresh", in_wav)
    )
    res_carried = IMPLEMENTATIONS["enhance"](
        in_wav, {}, _ctx(
            tmp_path / "carried", in_wav,
            analysis=dict(stats),
        ),
    )
    ya, _ = sf.read(str(res_fresh.path))
    yb, _ = sf.read(str(res_carried.path))
    assert np.allclose(np.asarray(ya), np.asarray(yb), atol=1e-4)


# ---- expander speech-mask gate (M4d fix round) --------------------------------


def _tone_with_quiet_tail(tmp_path: Path) -> tuple[Path, float]:
    """2.4 s file: 0.5 s silence, 1.0 s 880 Hz tone at −25 dB, then a
    0.4 s quiet tail at −62 dB (a faded syllable offset), 0.5 s silence.
    The VAD speech interval is [0.5, 1.9] — the tail sits INSIDE speech."""
    sr = SR
    t = np.arange(int(2.4 * sr)) / sr
    sig = np.zeros(len(t), dtype=np.float32)
    body = (t >= 0.5) & (t < 1.5)
    tail = (t >= 1.5) & (t < 1.9)
    sig[body] = 10 ** (-25 / 20) * np.sin(2 * np.pi * 880 * t[body])
    sig[tail] = 10 ** (-62 / 20) * np.sin(2 * np.pi * 880 * t[tail])
    src = tmp_path / "tone_tail.wav"
    sf.write(str(src), sig, sr)
    return src, sr


def _tail_db(path: Path, sr: int) -> float:
    y, _ = sf.read(str(path), dtype="float64", always_2d=True)
    seg = y.mean(axis=1)[int(1.60 * sr): int(1.85 * sr)]
    return float(20 * np.log10(np.sqrt((seg ** 2).mean()) + 1e-12))


def test_enhance_expander_respects_speech_mask(tmp_path):
    """M4d fix: the downward expander must never attenuate INSIDE VAD
    speech (plus hangover) — a −62 dB syllable tail inside a carried
    speech interval keeps its level, while the same tail with an empty
    mask is still expanded ~30 dB (the mask is what protects it)."""
    src, sr = _tone_with_quiet_tail(tmp_path)
    # gate threshold = min(−40+10, −18−18) = −36 dB: the −62 dB tail is
    # 26 dB under it → ~39 dB of expansion when unmasked.
    base = {
        "noise_floor_dbfs": -40.0, "speech_dbfs": -18.0,
        "speech_intervals_dur": 2.4,
    }
    masked = IMPLEMENTATIONS["enhance"](
        src, {"deess": False}, _ctx(
            tmp_path / "m", src,
            analysis={**base, "speech_intervals": [(0.5, 1.9)]},
        ),
    )
    unmasked = IMPLEMENTATIONS["enhance"](
        src, {"deess": False}, _ctx(
            tmp_path / "u", src,
            analysis={**base, "speech_intervals": []},
        ),
    )
    in_tail = _tail_db(src, sr)
    d_masked = _tail_db(masked.path, sr) - in_tail
    d_unmasked = _tail_db(unmasked.path, sr) - in_tail
    assert d_unmasked <= -6.0, (
        f"control: tail should be expanded ({d_unmasked:+.1f} dB)"
    )
    assert abs(d_masked) <= 2.0, (
        f"expanders must not touch in-speech frames ({d_masked:+.1f} dB)"
    )


def _run_stage_list(work, wav, ops, caps, analysis, timeline, device="cpu"):
    """Mini runner: threads wav + timeline + one shared analysis dict
    through the op impls exactly like the real pipeline runner."""
    from backend.pipeline.device import resolve_device

    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    for op_id, params in ops:
        dev = resolve_device(device, op_id, params, caps)
        info = sf.info(str(wav))
        ctx = OpContext(
            work_dir=work, device=dev,
            duration_s=info.frames / info.samplerate,
            sample_rate=info.samplerate, channels=info.channels,
            report=lambda *a: None, should_cancel=lambda: False,
            caps=caps, timeline=timeline, analysis=analysis,
        )
        res = IMPLEMENTATIONS[op_id](wav, params, ctx)
        wav = res.path
        if res.timeline is not None:
            timeline = res.timeline
    return wav, timeline


def _vad_speech_s(wav: Path) -> float:
    """VAD speech seconds — the same instrument the order experiment uses."""
    import torch
    import soxr
    from silero_vad import get_speech_timestamps, load_silero_vad

    x, sr = sf.read(str(wav), dtype="float64", always_2d=True)
    x = x.mean(axis=1)
    ts = get_speech_timestamps(
        torch.from_numpy(soxr.resample(x, sr, 16000).astype(np.float32)),
        load_silero_vad(), sampling_rate=16000, return_seconds=True,
    )
    return float(sum(t["end"] - t["start"] for t in ts))


@pytest.mark.slow
def test_podcast_chain_preserves_speech_real(tmp_path, real_dir):
    """M4d gate on real recordings (fix round): two assertions per file.

    1. ``enhance`` must keep >=99 % of the speech that survived the
       silence cut — the masked expander may never attenuate inside VAD
       speech (pre-fix it dropped ~3.3 s of quiet in-speech tails on
       Recording (3)).
    2. The final output must keep >=98 % of the *level-matched* cut
       speech: the silence output loudnorm'd to the same −16 LUFS the
       final gets, so the pure-gain VAD rescoring cancels (verified: a
       bare +15 dB move shifts ~0.4 s of borderline frames on
       Recording (3) — identical in the pre-M4d order, which is why the
       experiment's new-vs-old comparison is the real end-to-end gate;
       this bound is a tripwire for actual speech loss beyond that)."""
    from backend.media.decode import decode_to_wav
    from backend.media.ffmpeg_run import run_ffmpeg
    from backend.operations.registry import PRESETS
    from backend.operations.rules import resolve, unavailable_code
    from backend.core.capabilities import detect_capabilities
    from backend.operations.registry import OPERATIONS as OPS

    caps = detect_capabilities()
    avail = {
        k: unavailable_code(spec, caps.modules, caps.models) is None
        for k, spec in OPS.items()
    }
    ops = resolve(dict(PRESETS["podcast"]["ops"]), avail).ops
    cut = next(i for i, (op_id, _p) in enumerate(ops) if op_id == "silence")
    pre, post = ops[: cut + 1], ops[cut + 1:]
    enh_at = next(i for i, (op_id, _p) in enumerate(post) if op_id == "enhance")

    report = []
    for real in sorted(real_dir.glob("*.m4a")):
        job = tmp_path / real.stem.replace(" ", "_")
        job.mkdir()
        p = probe(real)
        wav = decode_to_wav(
            real, job / "in.wav", p.audio[0].index
        ).path
        # one shared analysis dict across BOTH halves, like the runner
        analysis: dict = {}
        timeline = None
        wav, timeline = _run_stage_list(
            job / "w", wav, pre, caps, analysis, timeline)
        silence_out = wav
        speech_cut = _vad_speech_s(silence_out)
        # level-matched reference for the final check (same −16 LUFS)
        normed = job / "w" / "sil_normed.wav"
        run_ffmpeg(
            ["-i", silence_out,
             "-af", "loudnorm=I=-16:TP=-1:LRA=11:linear=true",
             "-ar", "48000", "-c:a", "pcm_f32le", str(normed)],
            duration_s=p.duration_s, on_pct=lambda q: None,
            should_cancel=lambda: False, out_path=normed,
        )
        speech_ref = _vad_speech_s(normed)
        wav, timeline = _run_stage_list(
            job / "w", wav, post[: enh_at + 1], caps, analysis, timeline)
        speech_enh = _vad_speech_s(wav)
        wav, timeline = _run_stage_list(
            job / "w", wav, post[enh_at + 1:], caps, analysis, timeline)
        speech_out = _vad_speech_s(wav)
        pct_e = 100.0 * speech_enh / speech_cut
        pct_f = 100.0 * speech_out / speech_ref
        report.append(
            f"{real.name}: cut {speech_cut:.2f}s -> enh {speech_enh:.2f}s"
            f" ({pct_e:.1f}%), final {speech_out:.2f}s vs matched"
            f" {speech_ref:.2f}s ({pct_f:.1f}%)"
        )
        assert speech_enh >= 0.99 * speech_cut, (
            f"{real.name}: enhance kept {pct_e:.1f}% of post-cut speech"
            " — expander is attenuating inside speech"
        )
        assert speech_out >= 0.98 * speech_ref, (
            f"{real.name}: final speech {speech_out:.2f}s is {pct_f:.1f}%"
            f" of the level-matched {speech_ref:.2f}s"
        )
    print("\nspeech preservation: " + "; ".join(report))


# ---- loudness true-peak recheck ----------------------------------------------


def test_loudness_remeasured_true_peak(tmp_path):
    """After the corrective limiter pass the op re-measures TP —
    output must be ≤ −0.9 dBTP even when the correction had to run."""
    from backend.media.loudness import true_peak_db

    # −30 LUFS-ish quiet signal with spikes near 0 dBFS: loudnorm's
    # linear part raises it a lot; the TP stage + recheck must hold.
    rng = np.random.default_rng(3)
    t = np.arange(int(SR * 4)) / SR
    sig = (0.03 * np.sin(2 * np.pi * 300 * t)).astype(np.float32)
    for i in range(0, len(sig), SR // 3):
        sig[i:i + 4] += 0.6 * rng.choice([-1.0, 1.0], 4).astype(np.float32)
    src = tmp_path / "spiky.wav"
    sf.write(str(src), sig, SR)

    res = IMPLEMENTATIONS["loudness"](
        src, {"target": "podcast"}, _ctx(tmp_path / "work", src)
    )
    assert true_peak_db(res.path) <= -0.9
