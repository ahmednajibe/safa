"""GET /api/estimate — coarse runtime bucket for the dock subline (SPEC §9)."""
from __future__ import annotations

import json

from fastapi import APIRouter, Request

from backend.api.errors import err
from backend.core.capabilities import detect_capabilities
from backend.core.messages import MESSAGES
from backend.db import connect
from backend.pipeline.device import gpu_usable
from backend.pipeline.estimates import (
    bucket_code_for,
    bucket_for,
    estimate_seconds,
)

router = APIRouter()


def _ops_list(raw: str) -> list[tuple[str, dict]]:
    """Accept ``[{"id","params"}, ...]`` or ``{"op_id": {params}, ...}``."""
    data = json.loads(raw)
    if isinstance(data, dict):
        return [(k, v or {}) for k, v in data.items()]
    out = []
    for item in data or []:
        out.append((item["id"], item.get("params") or {}))
    return out


@router.get("/api/estimate")
def estimate(
    request: Request, media_id: str, device: str = "", ops: str = "[]"
):
    settings = request.app.state.settings
    con = connect(settings.db_path)
    try:
        row = con.execute(
            "SELECT probe_json FROM media WHERE id = ?", (media_id,)
        ).fetchone()
    finally:
        con.close()
    if row is None:
        return err(404, "media_not_found", MESSAGES["media_not_found"])
    try:
        duration_s = float(json.loads(row["probe_json"]).get("duration_s") or 0)
    except (json.JSONDecodeError, ValueError, TypeError):
        duration_s = 0.0
    try:
        op_list = _ops_list(ops)
    except (json.JSONDecodeError, TypeError, AttributeError, KeyError):
        return err(422, "bad_request", "Invalid ops parameter.")

    caps = detect_capabilities()
    dev = device or ("gpu" if gpu_usable(caps) else "cpu")
    if dev not in ("gpu", "cpu"):
        return err(422, "bad_request", "Invalid device.")
    if dev == "gpu" and not gpu_usable(caps):
        return err(422, "gpu_unavailable", MESSAGES["gpu_unavailable"])
    low, high = estimate_seconds(duration_s, op_list, dev, caps)
    bucket_code, minutes = bucket_code_for(high)
    return {
        "low_s": low,
        "high_s": high,
        "bucket": bucket_for(high),
        "bucket_code": bucket_code,
        "minutes": minutes,
    }
