"""separate — "Separate voice and music" (SPEC §2, M7 Task 2).

``output`` picks the stem mix: ``vocals`` → main wav is the voice;
``instrumental`` → main wav is the music (karaoke); ``both`` → main wav is
the voice *and* encoded ``<stem>_voice`` / ``<stem>_music`` files land in
``ctx.out_dir`` (the job's ``output/`` dir) as ``stem``-kind extra outputs
so they survive the runner's ``work/`` cleanup.

All engine detail lives in ``backend.media.separator``; GPU OOM falls back
to CPU per the SPEC §9 ladder and is reported via ``fallback_reason`` +
``actual_device`` (the runner turns it into a ``gpu_fallback`` warning).
"""
from __future__ import annotations

from pathlib import Path

from backend.core.messages import msg
from backend.media.encode import encode_audio
from backend.operations.base import OpContext, OpResult, stage_wav
from backend.pipeline.types import OutputFile


def run(in_wav: Path, params: dict | None, ctx: OpContext) -> OpResult:
    params = params or {}
    output = params.get("output", "vocals")
    model = params.get("model", "htdemucs")
    out = stage_wav(ctx, "separate")
    requested = ctx.device

    from backend.media import separator

    voice_wav = out if output in ("vocals", "both") else None
    music_wav = (
        out
        if output == "instrumental"
        else (
            ctx.work_dir / "stage_30_separate_music.wav"
            if output == "both"
            else None
        )
    )
    actual = separator.process_file(
        in_wav,
        voice_wav,
        music_wav,
        model=model,
        device=requested,
        work_dir=ctx.work_dir,
        report=ctx.report,
        should_cancel=ctx.should_cancel,
    )

    extra: list[OutputFile] = []
    if output == "both":
        out_dir = Path(ctx.out_dir) if ctx.out_dir else ctx.work_dir
        stem = ctx.display_stem or in_wav.stem
        fmt = ctx.audio_format or "wav"
        for tag, wav in (("voice", voice_wav), ("music", music_wav)):
            dst = encode_audio(wav, out_dir / f"{stem}_{tag}", fmt)
            extra.append(
                OutputFile(dst.name, "stem", dst, dst.stat().st_size)
            )
    ctx.report(1.0, None)

    text, code, p = msg("separated", output=output)
    return OpResult(
        path=out,
        actual_device=actual,
        fallback_reason=(
            "GPU out of memory"
            if requested == "gpu" and actual == "cpu"
            else None
        ),
        message=text,
        message_code=code,
        message_params=p,
        extra_outputs=extra,
    )
