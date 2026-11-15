"""subtitles — "Create subtitles" (SPEC §2, M8 Task 3).

Runs last (order 90) so word timestamps are already in output time.
The audio itself passes through untouched — ``OpResult.path`` is the
input WAV; the deliverables are ``<stem>.srt`` / ``<stem>.vtt`` extra
outputs (kind ``subtitle``) written into ``ctx.out_dir`` so they survive
the runner's ``work/`` cleanup.

Engine detail lives in ``backend.media.transcribe`` (faster-whisper
loading, OOM→CPU ladder, silent-input guard) and
``backend.media.subtitle_format`` (cue packing + writers). A GPU OOM
surfaces here as ``actual_device="cpu"`` + ``fallback_reason`` — the
runner turns it into the standard ``gpu_fallback`` warning.
"""
from __future__ import annotations

from pathlib import Path

from backend.core.messages import msg
from backend.operations.base import OpContext, OpResult
from backend.pipeline.types import OutputFile


def run(in_wav: Path, params: dict | None, ctx: OpContext) -> OpResult:
    params = params or {}
    language = params.get("language", "auto")
    fmt = params.get("format", "srt")
    model = params.get("model", "fast")
    max_line_chars = int(params.get("max_line_chars", 42))
    max_lines = int(params.get("max_lines", 2))

    from backend.media import transcribe
    from backend.media.subtitle_format import (
        build_cues,
        write_srt,
        write_vtt,
    )

    res = transcribe.transcribe_file(
        in_wav,
        language=None if language == "auto" else language,
        model=model,
        device=ctx.device,
        report=ctx.report,
        should_cancel=ctx.should_cancel,
    )

    cues = build_cues(
        res.segments, ctx.duration_s, max_line_chars, max_lines
    )

    out_dir = Path(ctx.out_dir) if ctx.out_dir else ctx.work_dir
    stem = ctx.display_stem or in_wav.stem
    extra: list[OutputFile] = []
    if fmt in ("srt", "both"):
        p = write_srt(cues, out_dir / f"{stem}.srt")
        extra.append(OutputFile(p.name, "subtitle", p, p.stat().st_size))
    if fmt in ("vtt", "both"):
        p = write_vtt(cues, out_dir / f"{stem}.vtt")
        extra.append(OutputFile(p.name, "subtitle", p, p.stat().st_size))
    ctx.report(1.0, None)

    text, code, p = msg(
        "subtitles_written",
        count=len(cues),
        language=res.language or language,
    )
    return OpResult(
        path=in_wav,
        actual_device=res.device,
        fallback_reason=(
            ("GPU out of memory" if res.fallback_kind == "oom"
             else "GPU transcription failed — retried on CPU")
            if res.oom_fallback
            else None
        ),
        message=text,
        message_code=code,
        message_params=p,
        extra_outputs=extra,
    )
