"""/api/jobs — submit, list, SSE progress, cancel, retry, delete, downloads.

Submission snapshots everything the worker needs (SPEC §9): resolved ops in
execution order, output spec, device, preview window, audio stream. Progress
reaches the browser over SSE — the endpoint replays the job row, so a
reconnect always sees the full current state first.
"""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from backend.api.errors import err
from backend.api.media import _file_response, _insufficient, _media_type
from backend.core import output_dir
from backend.core.capabilities import detect_capabilities
from backend.core.disk import needed_bytes_for_job
from backend.core.messages import MESSAGES as _MSG
from backend.db import connect
from backend.jobs.models import TERMINAL, Job
from backend.media.encode import AUDIO_FORMATS
from backend.media.storage import free_space_bytes
from backend.operations.rules import (
    ModelMissingError, ParamError, resolve, unavailable_code,
)
from backend.operations.registry import OPERATIONS
from backend.pipeline.device import gpu_usable

router = APIRouter()

_POLL_S = 0.25
_HEARTBEAT_S = 15.0

_ID_RE = re.compile(r"[0-9a-f]{32}")


def _bad_id(job_id: str) -> bool:
    """R1 A3: only lowercase hex ids may reach the store/filesystem."""
    return _ID_RE.fullmatch(job_id) is None


def _store(request: Request):
    return request.app.state.store


def _job_payload(store, job: Job) -> dict:
    d = job.to_dict()
    d["queue_position"] = store.queue_position(job.id)
    return d


def _files_payload(store, job_id: str) -> list[dict]:
    """``files`` for job payloads — name/kind/size + where a copy was
    auto-saved (SPEC §12), None when only the app copy exists."""
    return [
        {
            "name": f["name"],
            "kind": f["kind"],
            "size_bytes": f["size_bytes"],
            "saved_path": f.get("saved_path"),
        }
        for f in store.files(job_id)
    ]


def _media_row(db_path: Path, media_id: str):
    con = connect(db_path)
    try:
        return con.execute(
            "SELECT id, filename, stored_path, size_bytes, probe_json"
            " FROM media WHERE id = ?",
            (media_id,),
        ).fetchone()
    finally:
        con.close()


# M10 T5: refuse to queue work the disk can't hold (intermediates +
# outputs + headroom) — mid-run ENOSPC costs the user a whole job.
_JOB_HEADROOM = 512 << 20


def _disk_check(
    settings,
    probe_d: dict,
    kind: str,
    preview: dict | None,
    output: dict,
    audio_stream: int,
    size_bytes: int,
) -> JSONResponse | None:
    """507 ``insufficient_storage`` when the estimated peak won't fit."""
    needed = (
        needed_bytes_for_job(
            probe_d, kind, preview, output, audio_stream, size_bytes
        )
        + _JOB_HEADROOM
    )
    free = free_space_bytes(settings.data_dir)
    return _insufficient(needed, free) if free < needed else None


def _norm_ops(ops: Any) -> dict[str, dict]:
    """Accept ``{"op": params}`` or ``[{"id","params"}]`` selection forms."""
    if ops is None:
        return {}
    if isinstance(ops, dict):
        return {str(k): (v or {}) for k, v in ops.items()}
    if isinstance(ops, list):
        out = {}
        for item in ops:
            out[str(item["id"])] = item.get("params") or {}
        return out
    raise ValueError("ops must be an object or a list")


def _availability() -> dict[str, bool]:
    caps = detect_capabilities()
    return {
        op_id: unavailable_code(op, caps.modules, caps.models) is None
        for op_id, op in OPERATIONS.items()
    }


class JobRequest(BaseModel):
    media_id: str
    ops: Any = None
    output: dict | None = None
    device: str | None = None
    preview: dict | None = None
    audio_stream: int | None = None


class KeepBody(BaseModel):
    keep: bool


@router.post("/api/jobs", status_code=201)
def create_job(request: Request, body: JobRequest):
    settings = request.app.state.settings
    store = _store(request)

    if _bad_id(body.media_id):
        return err(404, "media_not_found", _MSG["media_not_found"])
    row = _media_row(settings.db_path, body.media_id)
    if row is None:
        return err(404, "media_not_found", _MSG["media_not_found"])
    try:
        probe_d = json.loads(row["probe_json"] or "{}")
    except json.JSONDecodeError:
        probe_d = {}
    audio_streams = {a.get("index") for a in probe_d.get("audio") or []}

    caps = detect_capabilities()
    device = body.device or ("gpu" if gpu_usable(caps) else "cpu")
    if device not in ("gpu", "cpu"):
        return err(422, "bad_device", _MSG["bad_device"])
    if device == "gpu" and not gpu_usable(caps):
        return err(422, "gpu_unavailable", _MSG["gpu_unavailable"])

    try:
        selection = _norm_ops(body.ops)
    except (ValueError, TypeError, KeyError) as exc:
        return err(422, "bad_request", str(exc))
    try:
        eff = resolve(selection, _availability(), models=caps.models)
    except ModelMissingError as exc:
        # R1 B10: the chosen model isn't downloaded — name it.
        return err(
            422, "model_missing", _MSG["model_missing"],
            params=exc.params,
        )
    except ParamError as exc:
        return err(422, "bad_param", str(exc), params=exc.params)
    except ValueError as exc:
        return err(422, "bad_request", str(exc))
    # Empty resolved ops = pass-through conversion — allowed.

    output = dict(body.output or {})
    fmt = output.get("audio_format", "wav")
    if fmt not in AUDIO_FORMATS:
        return err(422, "bad_output", _MSG["bad_output"])
    output = {
        "audio_format": fmt,
        "keep_video": bool(output.get("keep_video", True)),
    }

    preview = None
    kind = "full"
    if body.preview is not None:
        try:
            start_s = float(body.preview["start_s"])
            end_s = float(body.preview["end_s"])
        except (KeyError, TypeError, ValueError):
            return err(422, "bad_preview", _MSG["bad_preview"])
        length = end_s - start_s
        if start_s < 0 or not (1.0 <= length <= 60.0):
            return err(422, "bad_preview", _MSG["bad_preview"])
        preview = {"start_s": start_s, "end_s": end_s}
        kind = "preview"

    if body.audio_stream is not None:
        if body.audio_stream not in audio_streams:
            return err(
                422, "no_such_stream", _MSG["no_such_stream"]
            )
        audio_stream = body.audio_stream
    else:
        audio_stream = min(audio_streams) if audio_streams else 0

    too_full = _disk_check(
        settings, probe_d, kind, preview, output, audio_stream,
        row["size_bytes"] or 0,
    )
    if too_full is not None:
        return too_full

    job = store.create(
        media_id=body.media_id,
        kind=kind,
        ops=eff.ops,
        output=output,
        preview=preview,
        device=device,
        audio_stream=audio_stream,
    )
    if eff.disabled:
        # Some selected ops were dropped (conflict/availability): record it
        # as a job warning so the UI can show it after a reconnect too.
        store.add_warning(
            job.id, "preset_partial",
            {"skipped_op_ids": sorted(eff.disabled)},
        )
        job = store.get(job.id)
    request.app.state.supervisor.wake()
    payload = _job_payload(store, job)
    payload["disabled"] = eff.disabled
    return JSONResponse(payload, status_code=201)


@router.get("/api/jobs")
def list_jobs(request: Request):
    settings = request.app.state.settings
    con = connect(settings.db_path)
    try:
        rows = con.execute(
            "SELECT jobs.*, media.filename AS media_filename FROM jobs"
            " LEFT JOIN media ON media.id = jobs.media_id"
            " ORDER BY jobs.created_at DESC, jobs.id DESC LIMIT 100"
        ).fetchall()
    finally:
        con.close()
    store = _store(request)
    out = []
    for r in rows:
        d = _job_payload(store, Job.from_row(r))
        d["filename"] = r["media_filename"]
        # M9a Task 0: history rows carry output files (like GET /jobs/{id}
        # and every SSE state event) so the drawer needs no per-row fetch.
        d["files"] = _files_payload(store, r["id"])
        out.append(d)
    return {"jobs": out}


@router.get("/api/jobs/{job_id}")
def get_job(request: Request, job_id: str):
    store = _store(request)
    if _bad_id(job_id):
        return err(404, "job_not_found", _MSG["job_not_found"])
    job = store.get(job_id)
    if job is None:
        return err(404, "job_not_found", _MSG["job_not_found"])
    d = _job_payload(store, job)
    d["files"] = _files_payload(store, job_id)
    return d


@router.get("/api/jobs/{job_id}/events")
async def job_events(request: Request, job_id: str):
    store = _store(request)
    if _bad_id(job_id):
        return err(404, "job_not_found", _MSG["job_not_found"])
    if await run_in_threadpool(store.get, job_id) is None:
        return err(404, "job_not_found", _MSG["job_not_found"])

    async def stream():
        # R1 B4: an async generator — the poll loop sleeps on the event
        # loop, DB hits ride the threadpool, and a disconnected client is
        # detected on the next pass instead of a thread leaking forever.
        last_payload = None
        last_ping = time.monotonic()
        while True:
            if await request.is_disconnected():
                return
            job = await run_in_threadpool(store.get, job_id)
            if job is None:
                yield "event: done\ndata: {}\n\n"
                return
            payload = _job_payload(store, job)
            # files ride every state event so a reconnect sees them (M9).
            payload["files"] = await run_in_threadpool(
                _files_payload, store, job_id
            )
            text = json.dumps(payload, ensure_ascii=False)
            if text != last_payload:
                yield f"event: state\ndata: {text}\n\n"
                last_payload = text
            if job.state in TERMINAL:
                yield f"event: done\ndata: {text}\n\n"
                return
            now = time.monotonic()
            if now - last_ping >= _HEARTBEAT_S:
                yield ": ping\n\n"
                last_ping = now
            await asyncio.sleep(_POLL_S)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


@router.post("/api/jobs/{job_id}/cancel", status_code=202)
def cancel_job(request: Request, job_id: str):
    store = _store(request)
    if _bad_id(job_id):
        return err(404, "job_not_found", _MSG["job_not_found"])
    job = store.get(job_id)
    if job is None:
        return err(404, "job_not_found", _MSG["job_not_found"])
    if job.state in TERMINAL:
        return err(409, "not_terminal", _MSG["not_terminal"])
    request.app.state.supervisor.cancel(job_id)
    return _job_payload(store, store.get(job_id))


@router.post("/api/jobs/{job_id}/retry", status_code=201)
def retry_job(request: Request, job_id: str):
    settings = request.app.state.settings
    store = _store(request)
    if _bad_id(job_id):
        return err(404, "job_not_found", _MSG["job_not_found"])
    job = store.get(job_id)
    if job is None:
        return err(404, "job_not_found", _MSG["job_not_found"])
    if job.state not in ("failed", "cancelled", "interrupted"):
        return err(409, "not_retryable", _MSG["not_retryable"])
    row = _media_row(settings.db_path, job.media_id)
    if row is None:
        return err(404, "media_not_found", _MSG["media_not_found"])
    try:
        probe_d = json.loads(row["probe_json"] or "{}")
    except json.JSONDecodeError:
        probe_d = {}
    too_full = _disk_check(
        settings, probe_d, job.kind, job.preview, job.output,
        job.audio_stream, row["size_bytes"] or 0,
    )
    if too_full is not None:
        return too_full
    new_job = store.create(
        media_id=job.media_id,
        kind=job.kind,
        ops=job.ops,
        output=job.output,
        preview=job.preview,
        device=job.requested_device,
        audio_stream=job.audio_stream,
        retry_of=job.id,
    )
    request.app.state.supervisor.wake()
    return JSONResponse(_job_payload(store, new_job), status_code=201)


@router.post("/api/jobs/{job_id}/keep")
def keep_job(request: Request, job_id: str, body: KeepBody):
    """Per-job Keep flag (M10 T6): exempts the job from the retention
    sweep — the History drawer shows "Kept" instead of an expiry."""
    store = _store(request)
    if _bad_id(job_id):
        return err(404, "job_not_found", _MSG["job_not_found"])
    if store.get(job_id) is None:
        return err(404, "job_not_found", _MSG["job_not_found"])
    store.set_keep(job_id, body.keep)
    return _job_payload(store, store.get(job_id))


@router.delete("/api/jobs/{job_id}", status_code=204)
def delete_job(request: Request, job_id: str):
    settings = request.app.state.settings
    store = _store(request)
    if _bad_id(job_id):
        return err(404, "job_not_found", _MSG["job_not_found"])
    job = store.get(job_id)
    if job is None:
        return err(404, "job_not_found", _MSG["job_not_found"])
    if job.state in ("queued", "running"):
        return err(409, "not_terminal", _MSG["not_terminal"])
    store.delete(job_id)
    shutil.rmtree(
        settings.data_dir / "jobs" / job_id, ignore_errors=True
    )
    return Response(status_code=204)


@router.get("/api/jobs/{job_id}/timeline")
def job_timeline(request: Request, job_id: str):
    """The job's source↔output map (A/B playhead, cut list, subtitles).

    404 until the job completes — the file is written at the end of the
    pipeline run.
    """
    store = _store(request)
    if _bad_id(job_id):
        return err(404, "job_not_found", _MSG["job_not_found"])
    if store.get(job_id) is None:
        return err(404, "job_not_found", _MSG["job_not_found"])
    path = (
        request.app.state.settings.data_dir
        / "jobs" / job_id / "output" / "timeline.json"
    )
    if not path.is_file():
        return err(404, "timeline_not_found", _MSG["timeline_not_found"])
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return err(404, "timeline_not_found", _MSG["timeline_not_found"])


@router.get("/api/jobs/{job_id}/peaks")
def job_peaks(request: Request, job_id: str):
    """Waveform peaks of the *processed* audio (A/B compare, M9a Task 0).

    Written by the runner right after the encode stage; 404 until the job
    is completed (a failed/cancelled job has no processed waveform).
    """
    store = _store(request)
    if _bad_id(job_id):
        return err(404, "job_not_found", _MSG["job_not_found"])
    job = store.get(job_id)
    if job is None:
        return err(404, "job_not_found", _MSG["job_not_found"])
    path = (
        request.app.state.settings.data_dir
        / "jobs" / job_id / "output" / "peaks.json"
    )
    if job.state != "completed" or not path.is_file():
        return err(404, "peaks_not_found", _MSG["peaks_not_found"])
    try:
        return JSONResponse(
            json.loads(path.read_text(encoding="utf-8"))
        )
    except (OSError, json.JSONDecodeError):
        return err(404, "peaks_not_found", _MSG["peaks_not_found"])


@router.get("/api/jobs/{job_id}/files/{name}")
def download_file(request: Request, job_id: str, name: str):
    store = _store(request)
    if _bad_id(job_id):
        return err(404, "job_not_found", _MSG["job_not_found"])
    if store.get(job_id) is None:
        return err(404, "job_not_found", _MSG["job_not_found"])
    match = next(
        (f for f in store.files(job_id) if f["name"] == name), None
    )
    if match is None:
        return err(404, "job_not_found", _MSG["job_not_found"])
    path = Path(match["path"])
    if not path.is_file():
        return err(404, "job_not_found", _MSG["job_not_found"])
    # M9a Task 0: media.py's Range-aware responder (206 + real content
    # type), with the UTF-8 download name restored on top.
    resp = _file_response(path, request, _media_type(path))
    resp.headers["Content-Disposition"] = (
        f"attachment; filename*=UTF-8''{quote(match['name'])}"
    )
    return resp


class SaveAsBody(BaseModel):
    """Empty JSON body on purpose: a JSON POST needs a CORS preflight, so a
    foreign page can't fire this with a simple form/no-cors request (the
    Host/Origin guard in main.py is the first line)."""

    model_config = {"extra": "ignore"}


@router.post("/api/jobs/{job_id}/files/{name}/save-as")
def save_file_as(request: Request, job_id: str, name: str, body: SaveAsBody):
    """v0.1.1 "Save as…": the server (on the user's own PC) shows the
    native Save As window and copies the output to the chosen path. The
    browser File System Access picker was cancelled by Chrome itself after
    the user pressed Save (AbortError → no file), and doesn't exist in
    Firefox. 503 ``picker_unavailable`` (no GUI: Docker/headless) tells the
    frontend to fall back to a plain download."""
    store = _store(request)
    if _bad_id(job_id):
        return err(404, "job_not_found", _MSG["job_not_found"])
    if store.get(job_id) is None:
        return err(404, "job_not_found", _MSG["job_not_found"])
    match = next(
        (f for f in store.files(job_id) if f["name"] == name), None
    )
    if match is None:
        return err(404, "job_not_found", _MSG["job_not_found"])
    src = Path(match["path"])
    if not src.is_file():
        return err(404, "job_not_found", _MSG["job_not_found"])
    if not output_dir.PICK_LOCK.acquire(blocking=False):
        return err(409, "picker_busy", _MSG["picker_busy"])
    try:
        try:
            chosen = output_dir.run_save_as(match["name"])
        except output_dir.PickerUnavailable:
            return err(503, "picker_unavailable", _MSG["picker_unavailable"])
        if not chosen:
            return {"status": "cancelled"}
        try:
            target = output_dir.validate_save_target(chosen, src.suffix)
        except output_dir.OutputDirInvalid:
            return err(422, "output_dir_invalid", _MSG["output_dir_invalid"])
        try:
            output_dir.copy_to(src, target)
        except OSError:
            return err(500, "save_failed", _MSG["save_failed"])
        store.set_saved_path(job_id, match["name"], target)
        return {"status": "saved", "path": str(target)}
    finally:
        output_dir.PICK_LOCK.release()


@router.post("/api/jobs/{job_id}/save-all")
def save_all_files(request: Request, job_id: str, body: SaveAsBody):
    """v0.1.1 "Save all to folder…": the server's native folder window
    opens once and every job output is copied into it under non-colliding
    names (``name (2).ext``). Same contract as ``save-as`` — the frontend
    falls back to per-file browser downloads on 503 ``picker_unavailable``.
    """
    store = _store(request)
    if _bad_id(job_id):
        return err(404, "job_not_found", _MSG["job_not_found"])
    if store.get(job_id) is None:
        return err(404, "job_not_found", _MSG["job_not_found"])
    if not output_dir.PICK_LOCK.acquire(blocking=False):
        return err(409, "picker_busy", _MSG["picker_busy"])
    try:
        try:
            picked = output_dir.run_picker()
        except output_dir.PickerUnavailable:
            return err(503, "picker_unavailable", _MSG["picker_unavailable"])
        if not picked:
            return {"status": "cancelled"}
        try:
            folder = output_dir.validate_output_dir(
                picked, allow_drive_root=True
            )
        except output_dir.OutputDirInvalid:
            return err(422, "output_dir_invalid", _MSG["output_dir_invalid"])
        try:
            for f in store.files(job_id):
                target = output_dir._auto_copy(Path(f["path"]), folder)
                store.set_saved_path(job_id, f["name"], target)
        except OSError:
            return err(500, "save_failed", _MSG["save_failed"])
        return {"status": "saved", "folder": str(folder)}
    finally:
        output_dir.PICK_LOCK.release()


@router.post("/api/jobs/{job_id}/files/{name}/reveal")
def reveal_file(request: Request, job_id: str, name: str):
    """'Show in folder': select the saved copy in File Explorer
    (SPEC §12). ``job_files.saved_path`` records where auto-save, Save As
    or Save all put the file — without it there is nothing to reveal."""
    store = _store(request)
    if _bad_id(job_id):
        return err(404, "job_not_found", _MSG["job_not_found"])
    if store.get(job_id) is None:
        return err(404, "job_not_found", _MSG["job_not_found"])
    match = next(
        (f for f in store.files(job_id) if f["name"] == name), None
    )
    if match is None:
        return err(404, "job_not_found", _MSG["job_not_found"])
    saved = match.get("saved_path")
    if not saved or not Path(saved).is_file():
        return err(404, "no_saved_path", _MSG["no_saved_path"])
    output_dir.reveal_in_explorer(Path(saved))
    return {"revealed": True, "path": saved}
