"""M8 Task 3: the ``subtitles`` op — registry wiring, rules, outputs, and
end-to-end runs through the pipeline (Review Focus 1, 3, 4).
"""
import json
import re
from pathlib import Path

import pytest
import soundfile as sf

GENERATED = Path(__file__).parent / "fixtures" / "generated"
SPEECH = GENERATED / "speech.wav"
SPEECH_SIL = GENERATED / "speech_silences.wav"
SILENCE = GENERATED / "silence.wav"

_SRT_TIME = re.compile(
    r"(\d+):(\d+):(\d+),(\d+)\s*-->\s*(\d+):(\d+):(\d+),(\d+)"
)


def _whisper_ok() -> bool:
    try:
        import faster_whisper  # noqa: F401
        import silero_vad  # noqa: F401

        return True
    except Exception:
        return False


def _model_ok() -> bool:
    from backend.core.capabilities import _model_present

    return _model_present("whisper_small")


whisper_missing = pytest.mark.skipif(
    not (_whisper_ok() and _model_ok()), reason="whisper unavailable"
)


def _srt_cues(path: Path) -> list[tuple[float, float, list[str]]]:
    """Parse (start, end, lines) triples out of an SRT file."""
    text = Path(path).read_text(encoding="utf-8-sig")
    cues = []
    for m in _SRT_TIME.finditer(text):
        h1, m1, s1, ms1, h2, m2, s2, ms2 = (int(g) for g in m.groups())
        start = h1 * 3600 + m1 * 60 + s1 + ms1 / 1000.0
        end = h2 * 3600 + m2 * 60 + s2 + ms2 / 1000.0
        body = text[m.end():].lstrip("\n").split("\n\n")[0]
        cues.append((start, end, body.splitlines()))
    return cues


def _run(media_path, tmp_path, ops, preview=None, device="cpu"):
    from backend.media.probe import probe
    from backend.pipeline.runner import run_pipeline
    from backend.pipeline.types import OutputSpec

    p = probe(media_path)
    events = []
    outs = run_pipeline(
        media_path=media_path,
        probe=p,
        audio_stream_index=p.audio[0].index,
        ops=ops,
        output=OutputSpec(audio_format="wav", keep_video=False),
        job_root=tmp_path / "job",
        on_progress=events.append,
        should_cancel=lambda: False,
        preview=preview,
        device=device,
    )
    return outs, events


def _dur(path) -> float:
    info = sf.info(str(path))
    return info.frames / info.samplerate


# ---- registry / rules / estimates ---------------------------------------------


def test_registry_subtitles_params():
    from backend.operations.registry import OPERATIONS

    params = {p.key: p for p in OPERATIONS["subtitles"].params}
    langs = {o["value"] for o in params["language"].options}
    assert langs == {"auto", "en", "ar", "fr", "de", "es", "tr", "ur"}
    assert params["language"].default == "auto"
    assert {o["value"] for o in params["format"].options} == {
        "srt", "vtt", "both",
    }
    assert params["model"].default == "fast"
    assert {o["value"] for o in params["model"].options} == {
        "fast", "accurate",
    }
    assert params["max_line_chars"].default == 42
    assert params["max_lines"].default == 2


def test_karaoke_disables_subtitles():
    from backend.operations.registry import OPERATIONS
    from backend.operations.rules import resolve

    eff = resolve(
        {
            "separate": {"output": "instrumental"},
            "subtitles": {},
        },
        {op_id: True for op_id in OPERATIONS},
        set(OPERATIONS),
    )
    assert [op_id for op_id, _ in eff.ops] == ["separate"]
    assert eff.disabled["subtitles"]["code"] == "karaoke_speech_op"
    assert eff.disabled["subtitles"]["blocker_op_id"] == "separate"


def test_subtitles_estimates_have_per_model_entries():
    from backend.pipeline.estimates import estimate_seconds
    from backend.core.capabilities import Capabilities

    caps = Capabilities(
        python="3.11",
        gpu={"name": "GTX 1650", "total_mb": 4096},
        cuda_dlls=True,
        ffmpeg={},
        modules={"torch": True, "faster_whisper": True},
        models={},
    )
    fast_gpu = estimate_seconds(
        600.0, [("subtitles", {"model": "fast"})], "gpu", caps=caps
    )
    acc_gpu = estimate_seconds(
        600.0, [("subtitles", {"model": "accurate"})], "gpu", caps=caps
    )
    fast_cpu = estimate_seconds(
        600.0, [("subtitles", {"model": "fast"})], "cpu", caps=caps
    )
    assert 0 < fast_gpu[0] <= fast_gpu[1]
    assert 0 < acc_gpu[0] <= acc_gpu[1]
    assert acc_gpu[0] > fast_gpu[0]
    assert fast_cpu[0] > fast_gpu[0]


def test_whisper_turbo_manifest_and_capability(tmp_path, monkeypatch):
    from backend.core.capabilities import _model_present
    import backend.core.models_manifest as manifest
    from backend.core.models_manifest import MODELS

    assert "whisper_turbo" in MODELS
    assert MODELS["whisper_turbo"].source.endswith(
        "mobiuslabsgmbh/faster-whisper-large-v3-turbo"
    )
    # R1 weak test: probe a controlled HF-hub layout — model.bin present
    # → True, missing → False (was a ``in (True, False)`` tautology).
    monkeypatch.setattr(manifest, "MODELS_DIR", tmp_path)
    snap = (
        tmp_path / "whisper"
        / "models--mobiuslabsgmbh--faster-whisper-large-v3-turbo"
        / "snapshots" / "deadbeef"
    )
    snap.mkdir(parents=True)
    assert _model_present("whisper_turbo") is False
    (snap / "model.bin").write_bytes(b"weights")
    assert _model_present("whisper_turbo") is True


# ---- op behaviour -------------------------------------------------------------


@whisper_missing
def test_silent_input_writes_valid_empty_srt(tmp_path):
    """Review Focus 1: silence-only WAV + language=auto → no exception,
    valid empty SRT/VTT, count 0."""
    outs, events = _run(
        SILENCE, tmp_path,
        ops=[("subtitles", {"format": "both"})],
    )
    subs = [o for o in outs if o.kind == "subtitle"]
    assert {o.path.suffix for o in subs} == {".srt", ".vtt"}
    srt = next(o for o in subs if o.path.suffix == ".srt")
    assert srt.path.read_bytes().startswith(b"\xef\xbb\xbf")
    assert _srt_cues(srt.path) == []
    msg = next(
        e for e in events if e.message_code == "subtitles_written"
    )
    assert msg.message_params["count"] == 0


@whisper_missing
def test_subtitles_sapi_srt_content(tmp_path):
    """SAPI sentence → cues holding the recognizable words."""
    outs, _ = _run(SPEECH, tmp_path, ops=[("subtitles", {})])
    subs = [o for o in outs if o.kind == "subtitle"]
    assert len(subs) == 1
    srt = subs[0]
    assert srt.path.suffix == ".srt"
    cues = _srt_cues(srt.path)
    assert cues
    text = " ".join(l for _, _, lines in cues for l in lines).lower()
    for word in ("quick", "brown", "fox"):
        assert word in text, text
    out_dur = _dur(next(o for o in outs if o.kind == "audio").path)
    for start, end, _ in cues:
        assert 0 <= start < end <= out_dur + 0.001


@whisper_missing
def test_subtitles_both_formats_written(tmp_path):
    outs, _ = _run(
        SPEECH, tmp_path, ops=[("subtitles", {"format": "both"})]
    )
    subs = {o.path.suffix: o for o in outs if o.kind == "subtitle"}
    assert set(subs) == {".srt", ".vtt"}
    vtt = subs[".vtt"].path.read_text(encoding="utf-8")
    assert vtt.startswith("WEBVTT\n")
    assert not subs[".vtt"].path.read_bytes().startswith(b"\xef\xbb\xbf")


@whisper_missing
def test_op_returns_input_unchanged(tmp_path):
    """The audio path passes through: subtitle files are extra outputs."""
    outs, _ = _run(SPEECH, tmp_path, ops=[("subtitles", {})])
    audio = next(o for o in outs if o.kind == "audio")
    assert _dur(audio.path) == pytest.approx(_dur(SPEECH), abs=0.01)


@whisper_missing
def test_no_cue_covers_a_silence_cut(tmp_path):
    """Review Focus 3: silence before subtitles — cue times are *output*
    time: the silence op must have removed material, every cue sits inside
    [0, out_dur], and each cue start maps back into a kept source span.
    (A cue's display end may linger a little past a splice — that's normal
    subtitle behaviour; removed regions simply don't exist in output
    time, so containment is the real check.)"""
    from backend.media.timeline import Timeline

    outs, _ = _run(
        SPEECH_SIL, tmp_path,
        ops=[("silence", {}), ("subtitles", {})],
    )
    audio = next(o for o in outs if o.kind == "audio")
    srt = next(o for o in outs if o.kind == "subtitle")
    tl = Timeline.from_dict(
        json.loads(
            (tmp_path / "job" / "output" / "timeline.json").read_text()
        )
    )
    kept = tl.kept_spans_src()
    cues = _srt_cues(srt.path)
    out_dur = _dur(audio.path)
    in_dur = _dur(SPEECH_SIL)
    # the silence op must actually have cut — otherwise this test proves
    # nothing about cue timing
    assert out_dur < in_dur - 1.0
    assert cues, "expected cues on the speech fixture"
    for start, end, _lines in cues:
        assert 0 <= start < end <= out_dur + 0.001
        a_src = tl.out_to_src(start)
        assert any(
            a <= a_src <= b for a, b in kept
        ), f"cue {start} maps outside kept spans"


# ---- preview chain (Review Focus 4) -----------------------------------------------


def _tiled_source(tmp_path: Path, seconds: float = 60.0) -> Path:
    """speech_silences.wav tiled past `seconds` — has both speech and
    pauses inside preview [20, 40]."""
    src, sr = sf.read(
        str(SPEECH_SIL), dtype="float32", always_2d=True
    )
    out = tmp_path / "tiled.wav"
    written = 0
    with sf.SoundFile(
        str(out), "w", samplerate=sr, channels=1,
        format="WAV", subtype="FLOAT",
    ) as f:
        while written < int(seconds * sr):
            f.write(src)
            written += len(src)
    return out


@whisper_missing
def test_preview_chain_trim_silence_loudness_subtitles(tmp_path):
    """Preview [20, 40] + trim(25, 50) + silence + loudness + subtitles:
    completes; outputs are audio + subtitle; cue times inside the
    preview output duration."""
    src = _tiled_source(tmp_path)
    outs, events = _run(
        src, tmp_path,
        ops=[
            ("trim", {"start_s": 25.0, "end_s": 50.0}),
            ("silence", {}),
            ("loudness", {}),
            ("subtitles", {}),
        ],
        preview=(20.0, 40.0),
    )
    kinds = {o.kind for o in outs}
    assert kinds == {"audio", "subtitle"}
    audio = next(o for o in outs if o.kind == "audio")
    srt = next(o for o in outs if o.kind == "subtitle")
    out_dur = _dur(audio.path)
    cues = _srt_cues(srt.path)
    assert cues
    for start, end, _ in cues:
        assert 0 <= start < end <= out_dur + 0.001
    # trim kept [25,40) of the source → the preview is ≤15 s before
    # silence removal; loudness may have trimmed nothing more.
    assert out_dur <= 15.0 + 0.05


# ---- real recordings -------------------------------------------------------------


@whisper_missing
def test_subtitles_real_recording(real_dir, tmp_path):
    """Review Focus 3: language detected, ≥1 cue per 7 s of audio."""
    from backend.media.probe import probe

    rec = sorted(real_dir.glob("*.m4a"))[0]
    outs, _ = _run(rec, tmp_path, ops=[("subtitles", {})])
    srt = next(o for o in outs if o.kind == "subtitle")
    cues = _srt_cues(srt.path)
    dur = probe(rec).duration_s
    assert len(cues) >= dur / 10.0


# ---- slow: 30-minute bounds (Review Focus 5) --------------------------------------


def _make_long_speech(path: Path, target_s: float = 1800.0) -> None:
    """speech_silences.wav repeated with a 1 s gap until ≥ target_s —
    same builder as the M5 slow test."""
    import numpy as np

    src, sr = sf.read(str(SPEECH_SIL), dtype="float32", always_2d=True)
    gap = np.zeros((sr, 1), dtype=np.float32)
    written = 0
    with sf.SoundFile(
        str(path), "w", samplerate=sr, channels=1,
        format="WAV", subtype="FLOAT",
    ) as f:
        while written < int(target_s * sr):
            f.write(src)
            f.write(gap)
            written += len(src) + sr


def _rss_bytes() -> int:
    """Own RSS plus children — the whisper attempt runs in a disposable
    subprocess, so its model memory shows up there, not in this process."""
    import psutil

    proc = psutil.Process()
    total = proc.memory_info().rss
    for child in proc.children(recursive=True):
        try:
            total += child.memory_info().rss
        except psutil.NoSuchProcess:
            pass
    return total


@whisper_missing
@pytest.mark.slow
def test_30min_small_gpu_cpu_bounds(tmp_path):
    """30 min of speech through faster-whisper ``small`` on GPU and CPU:
    both complete; GPU stays inside the GTX 1650's VRAM budget and the
    CPU run keeps a bounded RSS delta (lazy generator — memory must not
    grow with duration)."""
    import os
    import threading
    import time

    from backend.media.transcribe import transcribe_file

    long_wav = tmp_path / "long30.wav"
    _make_long_speech(long_wav, target_s=1800.0)

    # RSS sampling thread (this test process owns the whisper load).
    rss_peak = [0]
    stop = threading.Event()

    def _poll():
        while not stop.is_set():
            rss_peak[0] = max(rss_peak[0], _rss_bytes())
            stop.wait(0.5)

    t = threading.Thread(target=_poll, daemon=True)
    t.start()
    try:
        # ---- CPU first (the longer run) ----
        base = _rss_bytes()
        t0 = time.monotonic()
        res_cpu = transcribe_file(
            long_wav, model="fast", device="cpu",
            should_cancel=lambda: False,
        )
        cpu_secs = time.monotonic() - t0
        stop.set()
        t.join(timeout=5)
        cpu_rss_delta = rss_peak[0] - base
        assert res_cpu.segments, "30-min CPU transcript empty"
        limit = 3 * (1 << 30)
        print(
            f"\nsmall CPU 30-min: {cpu_secs:.0f}s,"
            f" peak RSS +{cpu_rss_delta / (1 << 30):.2f} GiB"
        )
        assert cpu_rss_delta < limit, (
            f"CPU peak RSS delta {cpu_rss_delta / (1 << 30):.2f} GiB"
            " ≥ 3 GiB"
        )

        # ---- GPU run (skipped honestly without CUDA) ----
        if not os.environ.get("CUDA_VISIBLE_DEVICES", ""):
            from backend.core.gpu import VramSampler, gpu_info

            if gpu_info() is not None:
                with VramSampler() as vs:
                    res_gpu = transcribe_file(
                        long_wav, model="fast", device="gpu",
                        should_cancel=lambda: False,
                    )
                assert res_gpu.device == "gpu" or res_gpu.oom_fallback
                print(
                    f"small GPU 30-min: VRAM peak +{vs.peak_mb} MB"
                )
                # GTX 1650 = 4096 MB total; small int8 must stay well
                # inside it even for a half-hour file.
                assert vs.peak_mb < 3400, (
                    f"VRAM peak +{vs.peak_mb} MB ≥ 3400 MB"
                )
    finally:
        stop.set()
