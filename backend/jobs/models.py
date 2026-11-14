"""Job model (SPEC §4): one row per job; settings snapshotted at submit.

``ops``/``output``/``preview``/``stages`` are stored as JSON in the jobs
table; this dataclass is the in-memory form shared by the API, the
supervisor and the worker.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field

TERMINAL = {"completed", "failed", "cancelled", "interrupted"}


def _json(value: str | None, default):
    if not value:
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default


@dataclass
class Job:
    id: str
    media_id: str
    kind: str
    ops: list[dict]
    output: dict
    preview: dict | None
    requested_device: str
    audio_stream: int
    state: str
    stage: str | None = None
    stage_idx: int | None = None
    total_stages: int | None = None
    stage_pct: float | None = None
    overall_pct: float | None = None
    eta_sec: float | None = None
    device: str | None = None
    message: str | None = None
    error: str | None = None
    stages: list[dict] = field(default_factory=list)
    created_at: str = ""
    started_at: str | None = None
    finished_at: str | None = None
    retry_of: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Job":
        r = dict(row)  # tolerates missing columns on unmigrated DBs
        return cls(
            id=r.get("id"),
            media_id=r.get("media_id"),
            kind=r.get("kind"),
            ops=_json(r.get("ops_json"), []),
            output=_json(r.get("output_json"), {}),
            preview=_json(r.get("preview_json"), None),
            requested_device=r.get("requested_device") or "cpu",
            audio_stream=r.get("audio_stream") or 0,
            state=r.get("state"),
            stage=r.get("stage"),
            stage_idx=r.get("stage_idx"),
            total_stages=r.get("total_stages"),
            stage_pct=r.get("stage_pct"),
            overall_pct=r.get("overall_pct"),
            eta_sec=r.get("eta_sec"),
            device=r.get("device"),
            message=r.get("message"),
            error=r.get("error"),
            stages=_json(r.get("stages_json"), []),
            created_at=r.get("created_at") or "",
            started_at=r.get("started_at"),
            finished_at=r.get("finished_at"),
            retry_of=r.get("retry_of"),
        )
