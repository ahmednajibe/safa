"""SQLite-backed job store (M3b plan Task 1): single source of truth.

Every method opens a short-lived connection (WAL journal) so the API
process, the supervisor thread and the worker subprocess can read/write the
same database concurrently. Progress writes are throttled to <= 4/s per
job; stage changes are always written immediately.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from backend.core.capabilities import detect_capabilities
from backend.db import connect
from backend.jobs.models import Job
from backend.media.storage import new_id
from backend.operations.registry import OPERATIONS
from backend.pipeline.device import resolve_device
from backend.pipeline.types import OutputFile, Progress

_THROTTLE_S = 0.25  # max 4 progress writes per second per job
_INTERRUPTED_MSG = "Stopped because the app was closed"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm_ops(ops) -> list[dict]:
    """``[(op_id, params)]`` or ``[{id, params}]`` → canonical dict list."""
    out = []
    for item in ops or []:
        if isinstance(item, dict):
            out.append({"id": item["id"], "params": item.get("params") or {}})
        else:
            op_id, params = item
            out.append({"id": op_id, "params": params or {}})
    return out


def _expected_stages(ops, job_device: str) -> list[dict]:
    """One ``{id,name,device,fallback_reason}`` per resolved op (SPEC §9)."""
    caps = detect_capabilities()
    stages = []
    for op in _norm_ops(ops):
        spec = OPERATIONS.get(op["id"])
        stages.append(
            {
                "id": op["id"],
                "name": spec.name if spec else op["id"],
                "device": resolve_device(
                    job_device, op["id"], op["params"], caps
                ),
                "fallback_reason": None,
            }
        )
    return stages


class JobStore:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self._lock = threading.Lock()
        # job_id -> (last write monotonic, last stage name)
        self._last_progress: dict[str, tuple[float, str | None]] = {}

    # ---- helpers -----------------------------------------------------------

    def _con(self):
        return connect(self.db_path)

    @staticmethod
    def _get(row_factory_con, job_id: str):
        return row_factory_con.execute(
            "SELECT * FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()

    # ---- create / read -------------------------------------------------------

    def create(
        self,
        media_id: str,
        kind: str,
        ops,
        output: dict,
        preview: dict | None,
        device: str,
        audio_stream: int,
        retry_of: str | None = None,
    ) -> Job:
        job_id = new_id()
        ops_norm = _norm_ops(ops)
        stages = _expected_stages(ops_norm, device)
        con = self._con()
        try:
            con.execute(
                "INSERT INTO jobs (id, media_id, kind, ops_json, output_json,"
                " preview_json, state, requested_device, audio_stream,"
                " stages_json, retry_of, cancel_requested, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    job_id,
                    media_id,
                    kind,
                    json.dumps(ops_norm),
                    json.dumps(output or {}),
                    json.dumps(preview) if preview else None,
                    "queued",
                    device,
                    audio_stream,
                    json.dumps(stages),
                    retry_of,
                    0,
                    _utcnow(),
                ),
            )
            con.commit()
        finally:
            con.close()
        job = self.get(job_id)
        assert job is not None
        return job

    def get(self, job_id: str) -> Job | None:
        con = self._con()
        try:
            row = self._get(con, job_id)
            return Job.from_row(row) if row else None
        finally:
            con.close()

    def list(self, limit: int = 100) -> list[Job]:
        con = self._con()
        try:
            rows = con.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [Job.from_row(r) for r in rows]
        finally:
            con.close()

    def next_queued(self) -> Job | None:
        con = self._con()
        try:
            row = con.execute(
                "SELECT * FROM jobs WHERE state = 'queued'"
                " ORDER BY created_at ASC, id ASC LIMIT 1"
            ).fetchone()
            return Job.from_row(row) if row else None
        finally:
            con.close()

    def queue_position(self, job_id: str) -> int | None:
        """1-based position among queued jobs; None if not queued."""
        con = self._con()
        try:
            rows = con.execute(
                "SELECT id FROM jobs WHERE state = 'queued'"
                " ORDER BY created_at ASC, id ASC"
            ).fetchall()
        finally:
            con.close()
        ids = [r["id"] for r in rows]
        return ids.index(job_id) + 1 if job_id in ids else None

    # ---- lifecycle ----------------------------------------------------------

    def mark_running(self, job_id: str, pid: int) -> None:
        con = self._con()
        try:
            # Only a queued job may enter running — a job cancelled while
            # waiting must not be resurrected by a racing spawn.
            con.execute(
                "UPDATE jobs SET state='running', worker_pid=?,"
                " started_at=? WHERE id=? AND state='queued'",
                (pid, _utcnow(), job_id),
            )
            con.commit()
        finally:
            con.close()

    def update_progress(self, job_id: str, p: Progress) -> None:
        """Throttled (<= 4/s); a stage change always writes immediately."""
        now = time.monotonic()
        with self._lock:
            last_ts, last_stage = self._last_progress.get(
                job_id, (0.0, None)
            )
            if p.stage == last_stage and now - last_ts < _THROTTLE_S:
                return
            self._last_progress[job_id] = (now, p.stage)
        con = self._con()
        try:
            con.execute(
                "UPDATE jobs SET stage=?, stage_idx=?, total_stages=?,"
                " stage_pct=?, overall_pct=?, device=?, message=?"
                " WHERE id=?",
                (
                    p.stage,
                    p.stage_idx,
                    p.total_stages,
                    p.stage_pct,
                    p.overall_pct,
                    p.device,
                    p.message,
                    job_id,
                ),
            )
            con.commit()
        finally:
            con.close()

    def set_stage_device(
        self,
        job_id: str,
        stage_id: str,
        device: str,
        fallback_reason: str | None = None,
    ) -> None:
        """Record the device a stage actually ran on + any fallback reason."""
        con = self._con()
        try:
            row = con.execute(
                "SELECT stages_json FROM jobs WHERE id=?", (job_id,)
            ).fetchone()
            if not row:
                return
            stages = json.loads(row["stages_json"] or "[]")
            for s in stages:
                if s.get("id") == stage_id:
                    s["device"] = device
                    s["fallback_reason"] = fallback_reason
            con.execute(
                "UPDATE jobs SET stages_json=? WHERE id=?",
                (json.dumps(stages), job_id),
            )
            con.commit()
        finally:
            con.close()

    def finish(
        self,
        job_id: str,
        state: str,
        error: str | None = None,
        message: str | None = None,
    ) -> None:
        con = self._con()
        try:
            # Terminal states are final: a late write from a dying/orphaned
            # worker must not flip cancelled/interrupted/completed back. The
            # one allowed transition is failed -> cancelled (cancel intent
            # wins when the worker "fails" because we killed its ffmpeg).
            con.execute(
                "UPDATE jobs SET state=?, error=?, message=?, finished_at=?"
                " WHERE id=? AND state NOT IN"
                " ('completed','cancelled','interrupted')",
                (state, error, message, _utcnow(), job_id),
            )
            con.commit()
        finally:
            con.close()

    def request_cancel(self, job_id: str) -> None:
        con = self._con()
        try:
            con.execute(
                "UPDATE jobs SET cancel_requested=1 WHERE id=?", (job_id,)
            )
            con.commit()
        finally:
            con.close()

    def cancel_requested(self, job_id: str) -> bool:
        con = self._con()
        try:
            row = con.execute(
                "SELECT cancel_requested FROM jobs WHERE id=?", (job_id,)
            ).fetchone()
            return bool(row and row["cancel_requested"])
        finally:
            con.close()

    def mark_interrupted_on_startup(self) -> int:
        """Every ``running`` job is a corpse from the last process →
        ``interrupted``. Queued jobs stay queued for the new supervisor."""
        con = self._con()
        try:
            cur = con.execute(
                "UPDATE jobs SET state='interrupted', message=?,"
                " finished_at=? WHERE state='running'",
                (_INTERRUPTED_MSG, _utcnow()),
            )
            con.commit()
            return cur.rowcount
        finally:
            con.close()

    def delete(self, job_id: str) -> None:
        con = self._con()
        try:
            con.execute("DELETE FROM job_files WHERE job_id=?", (job_id,))
            con.execute("DELETE FROM jobs WHERE id=?", (job_id,))
            con.commit()
        finally:
            con.close()

    # ---- output files ---------------------------------------------------------

    def add_file(self, job_id: str, f: OutputFile) -> None:
        con = self._con()
        try:
            con.execute(
                "INSERT OR REPLACE INTO job_files"
                " (job_id, name, kind, path, size_bytes)"
                " VALUES (?,?,?,?,?)",
                (job_id, f.name, f.kind, str(f.path), f.size_bytes),
            )
            con.commit()
        finally:
            con.close()

    def files(self, job_id: str) -> list[dict]:
        con = self._con()
        try:
            rows = con.execute(
                "SELECT name, kind, path, size_bytes FROM job_files"
                " WHERE job_id=? ORDER BY name",
                (job_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            con.close()
