"""Pipeline runner: decode → ops (IMPLEMENTATIONS dispatch) → encode → video.

Each op is an ``OpFn`` looked up in ``backend.operations.impl.IMPLEMENTATIONS``;
unimplemented ids still raise ``NotImplementedError`` at their stage. Op
progress/cancel/messages flow through ``OpContext.report``/``should_cancel``;
the previous intermediate is deleted after each successful op stage. The job
system (M3b) calls ``run_pipeline`` from the worker subprocess.
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Callable

import soundfile as sf

from backend.core.capabilities import detect_capabilities
from backend.core.ffmpeg import has_encoder
from backend.core.messages import msg
from backend.media.decode import decode_to_wav
from backend.media.encode import encode_audio, remux_video
from backend.media.probe import MediaProbe
from backend.media.storage import sanitize_stem
from backend.media.timeline import Timeline
from backend.media.video_render import render_video
from backend.operations.base import OpContext
from backend.operations.impl import IMPLEMENTATIONS
from backend.operations.registry import OPERATIONS
from backend.pipeline.device import resolve_device
from backend.pipeline.types import (
    CancelCheck,
    CancelledError,
    JobDevice,
    OutputFile,
    OutputSpec,
    Progress,
    ProgressCb,
)

# A stage's emit(): report(stage_pct 0..1 | None, message | None,
# message_code | None, message_params | None, device | None, ...).
# Bound to one stage's name/idx by the loop below. ``*msg(code, **kw)``
# splats straight into args 1–3 after the pct (SPEC §11).
StageEmit = Callable[..., None]


def _op_stage_name(op_id: str) -> str:
    spec = OPERATIONS.get(op_id)
    return spec.name if spec else op_id


def _not_impl(op_id: str) -> None:
    raise NotImplementedError(f"operation {op_id} not implemented yet")


def run_pipeline(
    media_path: Path,
    probe: MediaProbe,
    audio_stream_index: int,
    ops: list[tuple[str, dict]],
    output: OutputSpec,
    job_root: Path,
    on_progress: ProgressCb,
    should_cancel: CancelCheck,
    preview: tuple[float, float] | None = None,
    device: JobDevice = "gpu",
    display_name: str | None = None,
) -> list[OutputFile]:
    """Run the pipeline; returns output files in ``job_root/output/``."""
    media_path = Path(media_path)
    job_root = Path(job_root)
    work = job_root / "work"
    out_dir = job_root / "output"
    work.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    caps = detect_capabilities()
    src_wav = work / "source.wav"
    stem = sanitize_stem(display_name or media_path.stem)
    suffix = "preview" if preview is not None else "clean"
    want_video = probe.has_video and output.keep_video and preview is None

    outputs: list[OutputFile] = []
    # ``current`` threads the working wav + the composed timeline (source
    # time → current output time) through the stages; set by _decode.
    current = {"wav": src_wav, "timeline": None}
    fps = probe.video[0].fps if want_video else None
    total = 2 + len(ops) + (1 if want_video else 0)

    def _emit(name: str, idx: int, dev: str) -> StageEmit:
        def emit(
            pct: float | None = None,
            message: str | None = None,
            message_code: str | None = None,
            message_params: dict | None = None,
            device: str | None = None,
            warning_code: str | None = None,
            warning_params: dict | None = None,
            stage_seconds: float | None = None,
            fallback_reason: str | None = None,
        ) -> None:
            on_progress(
                Progress(
                    stage=name,
                    stage_idx=idx,
                    total_stages=total,
                    stage_pct=pct,
                    overall_pct=(
                        (idx + min(pct or 0.0, 1.0)) / total
                    ),
                    device=device or dev,
                    message=message,
                    message_code=message_code,
                    message_params=message_params,
                    warning_code=warning_code,
                    warning_params=warning_params,
                    stage_seconds=stage_seconds,
                    fallback_reason=fallback_reason,
                )
            )

        return emit

    def _decode(_emit_fn: StageEmit) -> None:
        start_s = preview[0] if preview else None
        end_s = preview[1] if preview else None
        info = decode_to_wav(
            media_path, src_wav, audio_stream_index,
            start_s=start_s, end_s=end_s,
        )
        # Identity over the decoded duration (preview jobs: the preview
        # length); timeline ops compose onto this in output-time space.
        current["timeline"] = Timeline.identity(info.duration_s)

    def _save_audio(_emit_fn: StageEmit) -> None:
        dst = encode_audio(
            current["wav"], out_dir / f"{stem}_{suffix}", output.audio_format
        )
        outputs.append(
            OutputFile(dst.name, "audio", dst, dst.stat().st_size)
        )

    def _rebuild(emit: StageEmit) -> None:
        v = probe.video[0]
        tl = current["timeline"] or Timeline.identity(0.0)
        if tl.is_identity:
            dst = remux_video(media_path, v.index, current["wav"],
                              out_dir / f"{stem}_{suffix}", v.codec)
        else:
            want_nvenc = device == "gpu" and has_encoder("h264_nvenc")
            dst, enc = render_video(
                media_path, v.index, current["wav"], tl,
                out_dir / f"{stem}_{suffix}", device=device,
                fps=v.fps or 25.0,
                report=lambda p, m=None: emit(p, m),
                should_cancel=should_cancel,
                script_path=work / "video_filter.txt",
            )
            if enc == "libx264" and want_nvenc:
                emit(1.0, *msg("nvenc_fallback"), "cpu")
            elif enc == "h264_nvenc":
                emit(1.0, device="gpu")
        outputs.append(
            OutputFile(dst.name, "video", dst, dst.stat().st_size)
        )

    def _op_stage(
        op_id: str, params: dict, dev: JobDevice
    ) -> Callable[[StageEmit], None]:
        def _stage(emit: StageEmit) -> None:
            fn = IMPLEMENTATIONS.get(op_id)
            if fn is None:
                _not_impl(op_id)
            t0 = time.monotonic()
            prev = current["wav"]
            info = sf.info(prev)
            ctx = OpContext(
                work_dir=work,
                device=dev,
                duration_s=info.frames / info.samplerate,
                sample_rate=info.samplerate,
                channels=info.channels,
                report=lambda p, m=None, c=None, pr=None: emit(p, m, c, pr),
                should_cancel=should_cancel,
                caps=caps,
                fps=fps,
                timeline=current["timeline"],
                out_dir=out_dir,
                audio_format=output.audio_format,
                display_stem=stem,
                preview=preview,
            )
            result = fn(prev, params or {}, ctx)
            out_wav = Path(result.path)
            if not out_wav.is_file():
                raise RuntimeError(f"operation {op_id} produced no output")
            if result.timeline is not None:
                current["timeline"] = result.timeline
            # Report the device's reality + any op message/fallback reason
            # (SPEC §9: worker persists actual_device via this Progress row;
            # SPEC §11: a stage off its planned device is a job warning).
            warning_code = result.warning_code
            warning_params = result.warning_params
            if result.actual_device != dev or result.fallback_reason:
                warning_code = "gpu_fallback"
                warning_params = {"op_id": op_id}
            emit(
                1.0,
                result.message or result.fallback_reason,
                result.message_code,
                result.message_params,
                result.actual_device,
                warning_code=warning_code,
                warning_params=warning_params,
                stage_seconds=time.monotonic() - t0,
                fallback_reason=result.fallback_reason,
            )
            if prev != out_wav and prev.parent == work:
                prev.unlink(missing_ok=True)  # previous intermediate dies
            current["wav"] = out_wav
            outputs.extend(result.extra_outputs)

        return _stage

    stages: list[tuple[str, str, Callable[[StageEmit], None]]] = [
        ("Reading file", "cpu", _decode)
    ]
    for op_id, params in ops:
        dev = resolve_device(device, op_id, params or {}, caps)
        stages.append((_op_stage_name(op_id), dev, _op_stage(op_id, params, dev)))
    stages.append(("Saving audio", "cpu", _save_audio))
    if want_video:
        stages.append(("Rebuilding video", "cpu", _rebuild))

    assert len(stages) == total
    for idx, (name, dev, fn) in enumerate(stages):
        if should_cancel():
            raise CancelledError()
        emit = _emit(name, idx, dev)
        emit(0.0)
        fn(emit)

    # The timeline map ships with every job (identity included): the UI
    # uses it for A/B playhead translation, the cut list and subtitles.
    # Not registered as a downloadable file — /api/jobs/{id}/timeline
    # serves it.
    tl = current["timeline"]
    if tl is not None:
        (out_dir / "timeline.json").write_text(
            json.dumps(tl.to_dict(), ensure_ascii=False),
            encoding="utf-8",
        )
    on_progress(
        Progress(
            stage="Done", stage_idx=total, total_stages=total,
            stage_pct=1.0, overall_pct=1.0,
        )
    )
    shutil.rmtree(work, ignore_errors=True)  # intermediates die on success
    return outputs
