"""Pipeline runner skeleton (M3a): decode → ops → encode → optional remux.

Ops are a dispatch table of stage functions; none are implemented yet, so any
op id raises NotImplementedError at its stage. The job system (M3b) calls
``run_pipeline`` from the worker subprocess.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from backend.core.capabilities import detect_capabilities
from backend.media.decode import decode_to_wav
from backend.media.encode import encode_audio, remux_video
from backend.media.probe import MediaProbe
from backend.media.storage import sanitize_stem
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
    stem = sanitize_stem(media_path.stem)
    suffix = "preview" if preview is not None else "clean"
    want_video = probe.has_video and output.keep_video and preview is None

    outputs: list[OutputFile] = []

    def _decode() -> None:
        start_s = preview[0] if preview else None
        end_s = preview[1] if preview else None
        decode_to_wav(
            media_path, src_wav, audio_stream_index,
            start_s=start_s, end_s=end_s,
        )

    def _save_audio() -> None:
        dst = encode_audio(
            src_wav, out_dir / f"{stem}_{suffix}", output.audio_format
        )
        outputs.append(
            OutputFile(dst.name, "audio", dst, dst.stat().st_size)
        )

    def _remux() -> None:
        v = probe.video[0]
        dst = remux_video(media_path, v.index, src_wav,
                          out_dir / f"{stem}_{suffix}", v.codec)
        outputs.append(
            OutputFile(dst.name, "video", dst, dst.stat().st_size)
        )

    stages: list[tuple[str, str, object]] = [("Reading file", "cpu", _decode)]
    for op_id, params in ops:
        dev = resolve_device(device, op_id, params or {}, caps)
        stages.append(
            (_op_stage_name(op_id), dev,
             (lambda oid: lambda: _not_impl(oid))(op_id))
        )
    stages.append(("Saving audio", "cpu", _save_audio))
    if want_video:
        stages.append(("Rebuilding video", "cpu", _remux))

    total = len(stages)
    for idx, (name, dev, fn) in enumerate(stages):
        if should_cancel():
            raise CancelledError()
        on_progress(
            Progress(
                stage=name, stage_idx=idx, total_stages=total,
                stage_pct=0.0, overall_pct=idx / total, device=dev,
            )
        )
        fn()  # type: ignore[operator]

    on_progress(
        Progress(
            stage="Done", stage_idx=total, total_stages=total,
            stage_pct=1.0, overall_pct=1.0,
        )
    )
    shutil.rmtree(work, ignore_errors=True)  # intermediates die on success
    return outputs
