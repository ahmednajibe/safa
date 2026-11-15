"""Worker subprocess entry point (M3b plan Task 2): ``python -m backend.worker <job_id>``.

Runs ``run_pipeline`` for one job: loads the snapshotted job + media rows,
wires progress/cancel to the job store, registers output files and marks the
final state. Exit code 0 for completed/cancelled, 1 for failed.

The supervisor sets ``SOUND_SYSTEM_DB`` / ``SOUND_SYSTEM_DATA`` so a worker
targets the same store as the API; ``CUDA_VISIBLE_DEVICES=""`` already in the
environment is the hard CPU ceiling (SPEC §9) — nothing here touches it.
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path

from backend.core.messages import msg
from backend.core.proc import ProcError


def _prepend_ffmpeg_dir() -> None:
    """Same as the API start (FEASIBILITY workaround #2): children shell out
    to a bare ``ffmpeg``."""
    try:
        from backend.core.ffmpeg import find_ffmpeg

        bin_dir = str(find_ffmpeg().parent)
    except Exception:
        return
    if bin_dir not in os.environ.get("PATH", "").split(os.pathsep):
        os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get(
            "PATH", ""
        )


def _settings():
    """Settings honouring SOUND_SYSTEM_DATA / SOUND_SYSTEM_DB overrides."""
    from backend.core.settings import Settings, get_settings

    base = get_settings()
    data = os.environ.get("SOUND_SYSTEM_DATA")
    db = os.environ.get("SOUND_SYSTEM_DB")
    return Settings(
        host=base.host,
        port=base.port,
        data_dir=Path(data) if data else base.data_dir,
        db_path=Path(db) if db else base.db_path,
        retention_days=base.retention_days,
        max_upload_mb=base.max_upload_mb,
    )


def _load_media(db_path: Path, media_id: str):
    """(media_path, MediaProbe, filename) from the media row; None when gone."""
    from backend.db import connect
    from backend.media.probe import AudioStream, MediaProbe, VideoStream

    con = connect(db_path)
    try:
        row = con.execute(
            "SELECT stored_path, filename, probe_json FROM media WHERE id=?",
            (media_id,),
        ).fetchone()
    finally:
        con.close()
    if row is None:
        return None
    try:
        d = json.loads(row["probe_json"] or "{}")
    except json.JSONDecodeError:
        d = {}
    p = MediaProbe(
        duration_s=float(d.get("duration_s") or 0.0),
        format_name=d.get("format_name") or "",
        size_bytes=int(d.get("size_bytes") or 0),
        audio=[AudioStream(**a) for a in d.get("audio") or []],
        video=[VideoStream(**v) for v in d.get("video") or []],
    )
    return Path(row["stored_path"]), p, row["filename"] or ""


def _short_error(exc: BaseException) -> str:
    """One short human sentence for job.error."""
    if isinstance(exc, ProcError):
        tail = (exc.stderr_tail or "").strip().splitlines()
        last = tail[-1] if tail else str(exc)
        return f"A processing step failed: {last[:200]}"
    msg = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
    return msg[:300]


def _clear_dir(path: Path) -> None:
    import shutil

    if not path.is_dir():
        return
    for child in path.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: python -m backend.worker <job_id>", file=sys.stderr)
        return 1
    job_id = argv[0]

    from backend.core.models_manifest import configure_model_env

    configure_model_env()
    _prepend_ffmpeg_dir()

    settings = _settings()

    from backend.jobs.store import JobStore
    from backend.media.storage import job_dir
    from backend.pipeline.runner import run_pipeline
    from backend.pipeline.types import (
        CancelledError,
        OutputSpec,
        Progress,
    )

    job_root = job_dir(job_id, settings.data_dir)
    log_path = job_root / "worker.log"
    store = JobStore(settings.db_path)

    job = store.get(job_id)
    if job is None:
        log_path.write_text(
            f"job {job_id} not found in {settings.db_path}\n",
            encoding="utf-8",
        )
        return 1

    try:
        if job.requested_device == "gpu":
            from backend.core.gpu import setup_cuda_dlls

            setup_cuda_dlls()

        media = _load_media(settings.db_path, job.media_id)
        if media is None:
            raise RuntimeError("The uploaded file is gone.")
        media_path, media_probe, media_filename = media

        ops = [(o["id"], o["params"]) for o in job.ops]
        output = OutputSpec(
            audio_format=job.output.get("audio_format", "wav"),
            keep_video=bool(job.output.get("keep_video", True)),
        )
        preview = (
            (float(job.preview["start_s"]), float(job.preview["end_s"]))
            if job.preview
            else None
        )

        # Per-stage actual device bookkeeping (SPEC §9): the snapshot holds
        # the expected device; a stage reporting another one is a fallback —
        # persist it with the progress message as the reason.
        stage_by_name = {s.get("name"): s for s in job.stages}

        def _on_progress(p: Progress) -> None:
            try:
                store.update_progress(job_id, p)
                if p.warning_code:
                    store.add_warning(
                        job_id, p.warning_code, p.warning_params
                    )
                if p.device:
                    entry = stage_by_name.get(p.stage)
                    if entry and entry.get("device") != p.device:
                        store.set_stage_device(
                            job_id, entry["id"], p.device, p.message
                        )
                        entry["device"] = p.device
            except Exception:
                pass  # progress bookkeeping must never kill a job

        outputs = run_pipeline(
            media_path=media_path,
            probe=media_probe,
            audio_stream_index=job.audio_stream,
            ops=ops,
            output=output,
            job_root=job_root,
            on_progress=_on_progress,
            should_cancel=lambda: store.cancel_requested(job_id),
            preview=preview,
            device=job.requested_device,
            display_name=media_filename,
        )
        for f in outputs:
            store.add_file(job_id, f)
        text, code, params = msg("job_done")
        store.finish(
            job_id, "completed", message=text,
            message_code=code, message_params=params,
        )
        return 0
    except CancelledError:
        _clear_dir(job_root / "output")
        _clear_dir(job_root / "work")
        text, code, params = msg("job_cancelled")
        store.finish(
            job_id, "cancelled", message=text,
            message_code=code, message_params=params,
        )
        return 0
    except Exception as exc:  # noqa: BLE001 — every failure becomes job.error
        try:
            with log_path.open("a", encoding="utf-8") as fh:
                fh.write(traceback.format_exc())
        except OSError:
            pass
        # SPEC §11 job_failed{op_id?}: the stage the job died in identifies
        # the op when the stage name maps back to the snapshot's op ids.
        failed_op = None
        try:
            cur = store.get(job_id)
            if cur and cur.stage:
                for s in cur.stages:
                    if s.get("name") == cur.stage:
                        failed_op = s.get("id")
                        break
        except Exception:
            pass
        store.finish(
            job_id, "failed", error=_short_error(exc),
            error_code="job_failed",
            error_params={"op_id": failed_op} if failed_op else {},
        )
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
