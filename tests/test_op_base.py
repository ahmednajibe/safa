"""M4a Task 1: OpContext/OpResult interface, cancellable ffmpeg runner,
and runner wiring through IMPLEMENTATIONS."""
import shutil
import threading
import time
from pathlib import Path

import pytest
import soundfile as sf

from backend.media.ffmpeg_run import run_ffmpeg
from backend.media.probe import probe
from backend.operations.base import OpContext, OpResult
from backend.operations.impl import IMPLEMENTATIONS
from backend.pipeline.runner import run_pipeline
from backend.pipeline.types import CancelledError, OutputSpec

GENERATED = Path(__file__).parent / "fixtures" / "generated"
CLIP = GENERATED / "clip.mp4"


def test_run_ffmpeg_progress_monotonic(tmp_path):
    """out_time-driven progress callbacks are non-decreasing and finish."""
    out = tmp_path / "tone.wav"
    pcts: list[float] = []
    run_ffmpeg(
        [
            "-f", "lavfi", "-i", "sine=frequency=440:duration=5",
            "-ar", "48000", "-c:a", "pcm_f32le", out,
        ],
        duration_s=5.0,
        on_pct=pcts.append,
        should_cancel=lambda: False,
        out_path=out,
    )
    assert out.is_file()
    assert pcts, "no progress callbacks"
    assert pcts == sorted(pcts)
    assert pcts[-1] >= 99.0


def test_run_ffmpeg_cancel_kills(tmp_path):
    """Review Focus 3: cancel mid-encode → ffmpeg gone within 1 s,
    CancelledError raised, partial output deleted."""
    import psutil

    out = tmp_path / "long.wav"
    cancel = threading.Event()
    errors: list[BaseException] = []

    def go():
        try:
            run_ffmpeg(
                [
                    "-f", "lavfi",
                    "-i", "sine=frequency=440:duration=600",
                    "-af", "loudnorm=I=-16:TP=-1:LRA=11",
                    "-ar", "48000", "-c:a", "pcm_f32le", out,
                ],
                duration_s=600.0,
                on_pct=lambda p: None,
                should_cancel=cancel.is_set,
                out_path=out,
            )
        except BaseException as exc:  # noqa: BLE001 — capture for assert
            errors.append(exc)

    t = threading.Thread(target=go)
    t.start()
    time.sleep(0.8)  # let ffmpeg get going
    cancel.set()
    t_cancel = time.monotonic()
    t.join(timeout=10)
    gone_in = time.monotonic() - t_cancel

    assert not t.is_alive(), "run_ffmpeg did not return after cancel"
    assert errors and isinstance(errors[0], CancelledError)
    assert gone_in < 1.0, f"ffmpeg still alive {gone_in:.2f}s after cancel"
    assert not out.exists(), "partial output was left behind"
    leftovers = [
        p for p in psutil.Process().children(recursive=True)
        if "ffmpeg" in p.name().lower()
    ]
    assert leftovers == []


def test_run_ffmpeg_failure_raises_proc_error(tmp_path):
    from backend.core.proc import ProcError

    with pytest.raises(ProcError):
        run_ffmpeg(
            ["-i", str(tmp_path / "missing.wav"), "-f", "null", "-"],
            duration_s=None,
            on_pct=lambda p: None,
            should_cancel=lambda: False,
        )


def test_runner_uses_implementations(tmp_path, monkeypatch):
    """A fake op registered in IMPLEMENTATIONS runs as a pipeline stage:
    gets (in_wav, params, OpContext), its report() reaches Progress, and
    OpResult.message/actual_device are forwarded."""
    calls: list = []

    def fake(in_wav: Path, params: dict, ctx: OpContext) -> OpResult:
        calls.append((in_wav, dict(params)))
        assert isinstance(ctx, OpContext)
        assert ctx.sample_rate == 48000
        ctx.report(0.5, "halfway")
        out = ctx.work_dir / "fake_out.wav"
        shutil.copyfile(in_wav, out)
        return OpResult(path=out, actual_device="cpu", message="fake done")

    monkeypatch.setitem(IMPLEMENTATIONS, "fakeop", fake)

    p = probe(CLIP)
    events = []
    outs = run_pipeline(
        media_path=CLIP,
        probe=p,
        audio_stream_index=p.audio[0].index,
        ops=[("fakeop", {"x": 1})],
        output=OutputSpec(keep_video=False),
        job_root=tmp_path / "job",
        on_progress=events.append,
        should_cancel=lambda: False,
        device="cpu",
    )

    assert len(calls) == 1
    assert calls[0][1] == {"x": 1}
    assert calls[0][0].name == "source.wav"
    op_events = [e for e in events if e.stage == "fakeop"]
    assert any(e.stage_pct == 0.5 and e.message == "halfway" for e in op_events)
    assert any(e.message == "fake done" for e in op_events)
    assert all(e.device == "cpu" for e in op_events)
    assert len(outs) == 1 and outs[0].name.endswith(".wav")
    # input duration/channels came from the decoded wav
    info = sf.info(outs[0].path)
    assert info.samplerate == 48000


def test_runner_deletes_previous_intermediate(tmp_path, monkeypatch):
    """After a successful op stage the previous stage wav is deleted —
    observed by the next op: its input is the only earlier file left."""
    seen: dict = {}

    def fake1(in_wav: Path, params: dict, ctx: OpContext) -> OpResult:
        out = ctx.work_dir / "stage_98_fake1.wav"
        shutil.copyfile(in_wav, out)
        return OpResult(path=out)

    def fake2(in_wav: Path, params: dict, ctx: OpContext) -> OpResult:
        seen["files"] = sorted(p.name for p in ctx.work_dir.iterdir())
        seen["in_wav"] = in_wav.name
        out = ctx.work_dir / "stage_99_fake2.wav"
        shutil.copyfile(in_wav, out)
        return OpResult(path=out)

    monkeypatch.setitem(IMPLEMENTATIONS, "fake1", fake1)
    monkeypatch.setitem(IMPLEMENTATIONS, "fake2", fake2)
    p = probe(CLIP)
    run_pipeline(
        media_path=CLIP,
        probe=p,
        audio_stream_index=p.audio[0].index,
        ops=[("fake1", {}), ("fake2", {})],
        output=OutputSpec(keep_video=False),
        job_root=tmp_path / "job",
        on_progress=lambda e: None,
        should_cancel=lambda: False,
        device="cpu",
    )
    assert seen["in_wav"] == "stage_98_fake1.wav"
    assert "source.wav" not in seen["files"], "source.wav was not deleted"
