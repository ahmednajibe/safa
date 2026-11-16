"""M10 T3/T4: model download endpoints.

The heavy lifting runs in ``python -m backend.core.model_download`` so
the API process never imports torch/ONNX/ctranslate2 — the child
streams JSON-lines progress on stdout; a daemon thread drains it into
a per-model task snapshot the SSE endpoint replays.

  POST /api/models/{id}/download         → start (or join) a download
  GET  /api/models/{id}/download/events  → SSE progress/done/error/cancelled
  POST /api/models/{id}/download/cancel  → kill the child (``*.part`` stays)
  GET  /api/models                       → manifest + presence + sizes

Cancel is a process kill — ``.part`` files survive and the next attempt
resumes them. One in-flight task per model: a second POST joins it.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
import threading
import time

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from backend.api.errors import err
from backend.core.messages import MESSAGES as _MSG
from backend.core.models_manifest import MODELS
from backend.core.paths import ROOT

_log = logging.getLogger("sound_system")

router = APIRouter()

if sys.platform == "win32":
    _CREATEFLAGS = (
        subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    )
else:
    _CREATEFLAGS = 0


class _Task:
    """One download: child process + last-known progress snapshot."""

    __slots__ = (
        "model_id", "state", "pct", "file", "done_bytes", "total_bytes",
        "detail", "proc", "cancelled",
    )

    def __init__(self, model_id: str):
        self.model_id = model_id
        self.state = "downloading"  # downloading|done|error|cancelled
        self.pct: float | None = None
        self.file = ""
        self.done_bytes = 0
        self.total_bytes = 0
        self.detail = ""
        self.proc: subprocess.Popen | None = None
        self.cancelled = False

    def snapshot(self) -> dict:
        d = {
            "model_id": self.model_id,
            "state": self.state,
            "file": self.file,
            "done_bytes": self.done_bytes,
            "total_bytes": self.total_bytes,
        }
        if self.pct is not None:
            d["pct"] = self.pct
        if self.detail:
            d["detail"] = self.detail
        return d


class ModelDownloadManager:
    """Lives on ``app.state`` — one task per model, killable on shutdown."""

    def __init__(self) -> None:
        self._tasks: dict[str, _Task] = {}
        self._lock = threading.Lock()

    def _spawn(self, task: _Task) -> None:
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        # Child resolves data/models from the same root; model env is set
        # by model_download.main() itself (configure_model_env).
        task.proc = subprocess.Popen(
            [sys.executable, "-m", "backend.core.model_download",
             task.model_id],
            cwd=str(ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=_CREATEFLAGS,
        )
        threading.Thread(
            target=self._drain, args=(task,),
            name=f"dl-{task.model_id}", daemon=True,
        ).start()

    def _drain(self, task: _Task) -> None:
        assert task.proc is not None and task.proc.stdout is not None
        for line in task.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue  # library noise on stdout — only JSON lines count
            t = ev.get("type")
            if t == "progress":
                task.file = ev.get("file", task.file)
                task.done_bytes = ev.get("done_bytes", task.done_bytes)
                task.total_bytes = ev.get("total_bytes", task.total_bytes)
                if ev.get("pct") is not None:
                    task.pct = ev["pct"]
            elif t == "done":
                task.state = "done"
            elif t == "cancelled":
                task.state = "cancelled"
            elif t == "error":
                task.state = "error"
                task.detail = ev.get("detail", "")
        rc = task.proc.wait()
        if task.state == "downloading":
            task.state = (
                "cancelled" if (task.cancelled or rc == 3) else "error"
            )
            if task.state == "error" and not task.detail:
                task.detail = f"download exited ({rc})"
        if task.state == "done":
            try:
                from backend.core.capabilities import (
                    refresh_capabilities,
                )

                refresh_capabilities()
            except Exception:
                pass
        _log.info(
            "model %s download %s (rc %s)", task.model_id, task.state, rc
        )

    def start(self, model_id: str) -> _Task:
        with self._lock:
            task = self._tasks.get(model_id)
            if task is not None and task.state == "downloading":
                return task  # join the in-flight one
            task = _Task(model_id)
            self._tasks[model_id] = task
        self._spawn(task)
        return task

    def cancel(self, model_id: str) -> _Task | None:
        with self._lock:
            task = self._tasks.get(model_id)
        if task is None:
            return None
        task.cancelled = True
        if task.state == "downloading":
            proc = task.proc
            if proc is not None and proc.poll() is None:
                try:
                    proc.kill()
                except Exception:
                    pass
                task.state = "cancelled"
        return task

    def snapshot(self, model_id: str) -> dict | None:
        with self._lock:
            task = self._tasks.get(model_id)
        return task.snapshot() if task else None

    def stop(self) -> None:
        with self._lock:
            tasks = list(self._tasks.values())
        for task in tasks:
            if task.state == "downloading" and task.proc is not None:
                try:
                    task.proc.kill()
                except Exception:
                    pass


def _manager(request: Request) -> ModelDownloadManager:
    return request.app.state.model_downloads


def _present(model_id: str) -> bool:
    try:
        from backend.core.capabilities import _model_present

        return _model_present(model_id)
    except Exception:
        return False


def _unknown(model_id: str):
    return err(404, "bad_request", _MSG["bad_request"])


@router.get("/api/models")
def list_models(request: Request):
    from backend.core.capabilities import detect_capabilities

    status = detect_capabilities().models
    return {
        "models": [
            {
                "id": mid,
                "name": spec.name,
                "engine": spec.engine,
                "approx_mb": spec.approx_mb,
                "measured_bytes": spec.measured_bytes,
                "present": bool(status.get(mid)),
                "downloading": bool(
                    (s := _manager(request).snapshot(mid))
                    and s["state"] == "downloading"
                ),
            }
            for mid, spec in MODELS.items()
        ]
    }


@router.post("/api/models/{model_id}/download", status_code=202)
def start_download(request: Request, model_id: str):
    if model_id not in MODELS:
        return _unknown(model_id)
    if _present(model_id):
        return {"model_id": model_id, "state": "done", "already": True}
    task = _manager(request).start(model_id)
    return task.snapshot()


@router.post("/api/models/{model_id}/download/cancel", status_code=202)
def cancel_download(request: Request, model_id: str):
    if model_id not in MODELS:
        return _unknown(model_id)
    task = _manager(request).cancel(model_id)
    if task is None:
        return {"model_id": model_id, "state": "idle"}
    return task.snapshot()


@router.get("/api/models/{model_id}/download/events")
async def download_events(request: Request, model_id: str):
    if model_id not in MODELS:
        return _unknown(model_id)

    async def stream():
        mgr = _manager(request)
        last = None
        last_ping = time.monotonic()
        while True:
            if await request.is_disconnected():
                return
            snap = mgr.snapshot(model_id)
            if snap is None:
                # No task — still answer cleanly for a re-poll UI.
                state = "done" if _present(model_id) else "idle"
                text = json.dumps(
                    {"model_id": model_id, "state": state},
                    ensure_ascii=False,
                )
                yield f"event: state\ndata: {text}\n\n"
                yield f"event: done\ndata: {text}\n\n"
                return
            text = json.dumps(snap, ensure_ascii=False)
            if text != last:
                yield f"event: state\ndata: {text}\n\n"
                last = text
            if snap["state"] != "downloading":
                yield f"event: done\ndata: {text}\n\n"
                return
            now = time.monotonic()
            if now - last_ping >= 15:
                yield ": ping\n\n"
                last_ping = now
            await asyncio.sleep(0.4)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )
