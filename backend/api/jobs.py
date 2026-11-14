"""/api/jobs — submit, list, SSE progress, cancel, retry, delete, downloads.

Submission snapshots everything the worker needs (SPEC §9): resolved ops in
execution order, output spec, device, preview window, audio stream. Progress
reaches the browser over SSE — the endpoint replays the job row, so a
reconnect always sees the full current state first.
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

from backend.api.errors import err
from backend.core.capabilities import detect_capabilities
from backend.db import connect
from backend.jobs.models import TERMINAL, Job
from backend.media.encode import AUDIO_FORMATS
from backend.operations.rules import resolve, unavailable_code
from backend.operations.registry import OPERATIONS
from backend.pipeline.device import gpu_usable

router = APIRouter()

_POLL_S = 0.25
_HEARTBEAT_S = 15.0

_MSG = {
    "media_not_found": "This file was deleted or never existed.",
    "job_not_found": "This job was deleted or never existed.",
    "gpu_unavailable": "The graphics card isn't available on this computer.",
    "bad_device": "Invalid device.",
    "bad_output": "Unknown audio format.",
    "bad_preview": "The preview must be between 1 and 60 seconds.",
    "no_such_stream": "This file has no audio track with that number.",
    "not_terminal": "This job is still active.",
    "not_retryable": "Only failed, cancelled or interrupted jobs can be retried.",
    "timeline_not_found": "The timeline isn't available for this job yet.",
}


def _store(request: Request):
    return request.app.state.store


def _job_payload(store, job: Job) -> dict:
    d = job.to_dict()
    d["queue_position"] = store.queue_position(job.id)
    return d


def _media_row(db_path: Path, media_id: str):
    con = connect(db_path)
    try:
        return con.execute(
            "SELECT id, filename, stored_path, probe_json FROM media"
            " WHERE id = ?",
            (media_id,),
        ).fetchone()
    finally:
        con.close()


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


@router.post("/api/jobs", status_code=201)
def create_job(request: Request, body: JobRequest):
    settings = request.app.state.settings
    store = _store(request)

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
        eff = resolve(selection, _availability())
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

    job = store.create(
        media_id=body.media_id,
        kind=kind,
        ops=eff.ops,
        output=output,
        preview=preview,
        device=device,
        audio_stream=audio_stream,
    )
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
        out.append(d)
    return {"jobs": out}


@router.get("/api/jobs/{job_id}")
def get_job(request: Request, job_id: str):
    store = _store(request)
    job = store.get(job_id)
    if job is None:
        return err(404, "job_not_found", _MSG["job_not_found"])
    d = _job_payload(store, job)
    d["files"] = [
        {"name": f["name"], "kind": f["kind"], "size_bytes": f["size_bytes"]}
        for f in store.files(job_id)
    ]
    return d


@router.get("/api/jobs/{job_id}/events")
def job_events(request: Request, job_id: str):
    store = _store(request)
    if store.get(job_id) is None:
        return err(404, "job_not_found", _MSG["job_not_found"])

    def stream():
        last_payload = None
        last_ping = time.monotonic()
        while True:
            job = store.get(job_id)
            if job is None:
                yield "event: done\ndata: {}\n\n"
                return
            payload = json.dumps(
                _job_payload(store, job), ensure_ascii=False
            )
            if payload != last_payload:
                yield f"event: state\ndata: {payload}\n\n"
                last_payload = payload
            if job.state in TERMINAL:
                yield f"event: done\ndata: {payload}\n\n"
                return
            now = time.monotonic()
            if now - last_ping >= _HEARTBEAT_S:
                yield ": ping\n\n"
                last_ping = now
            time.sleep(_POLL_S)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


@router.post("/api/jobs/{job_id}/cancel", status_code=202)
def cancel_job(request: Request, job_id: str):
    store = _store(request)
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
    job = store.get(job_id)
    if job is None:
        return err(404, "job_not_found", _MSG["job_not_found"])
    if job.state not in ("failed", "cancelled", "interrupted"):
        return err(409, "not_retryable", _MSG["not_retryable"])
    if _media_row(settings.db_path, job.media_id) is None:
        return err(404, "media_not_found", _MSG["media_not_found"])
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


@router.delete("/api/jobs/{job_id}", status_code=204)
def delete_job(request: Request, job_id: str):
    settings = request.app.state.settings
    store = _store(request)
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


@router.get("/api/jobs/{job_id}/files/{name}")
def download_file(request: Request, job_id: str, name: str):
    store = _store(request)
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
    return FileResponse(
        path,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": "attachment; "
            f"filename*=UTF-8''{quote(match['name'])}"
        },
    )
