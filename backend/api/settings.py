"""/api/settings — the optional "Always save to" folder (SPEC §12).

GET reads the current value; PUT stores a pasted path; DELETE turns the
feature off; POST .../pick opens the native folder dialog via a short-lived
subprocess (single-flight + timeout in ``core.output_dir``). All mutating
endpoints are covered by the Host/Origin middleware in ``main.py``.
"""
from __future__ import annotations

import threading

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel

from backend.api.errors import err
from backend.core import output_dir
from backend.core.messages import MESSAGES as _MSG

router = APIRouter()

# Single-flight: one native dialog at a time — a second POST gets 409.
_PICK_LOCK = threading.Lock()


class OutputDirBody(BaseModel):
    path: str


def _settings_payload(store) -> dict:
    return {"output_dir": store.get_setting("output_dir")}


@router.get("/api/settings")
def get_settings(request: Request):
    return _settings_payload(request.app.state.store)


@router.put("/api/settings/output-dir")
def put_output_dir(request: Request, body: OutputDirBody):
    store = request.app.state.store
    try:
        resolved = output_dir.validate_output_dir(body.path)
    except output_dir.OutputDirInvalid:
        return err(
            422, "output_dir_invalid", _MSG["output_dir_invalid"]
        )
    store.set_setting("output_dir", str(resolved))
    return {
        "output_dir": str(resolved),
        "message_code": "output_dir_set",
        "message_params": {"path": str(resolved)},
    }


@router.delete("/api/settings/output-dir", status_code=204)
def delete_output_dir(request: Request):
    request.app.state.store.delete_setting("output_dir")
    return Response(status_code=204)


@router.post("/api/settings/output-dir/pick")
def pick_output_dir(request: Request):
    store = request.app.state.store
    if not _PICK_LOCK.acquire(blocking=False):
        return err(409, "picker_busy", _MSG["picker_busy"])
    try:
        try:
            picked = output_dir.run_picker()
        except output_dir.PickerUnavailable:
            return err(
                503, "picker_unavailable", _MSG["picker_unavailable"]
            )
        if not picked:
            # Cancelled / timed out — keep the previous setting.
            return {"cancelled": True, **_settings_payload(store)}
        try:
            resolved = output_dir.validate_output_dir(picked)
        except output_dir.OutputDirInvalid:
            return err(
                422, "output_dir_invalid", _MSG["output_dir_invalid"]
            )
        store.set_setting("output_dir", str(resolved))
        return {
            "cancelled": False,
            "output_dir": str(resolved),
            "message_code": "output_dir_set",
            "message_params": {"path": str(resolved)},
        }
    finally:
        _PICK_LOCK.release()
