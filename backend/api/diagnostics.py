"""GET /api/diagnostics — redacted report for "Copy diagnostics" (M10 T7).

Read-only GET; the Host guard still applies. Everything returned is
scrubbed of user paths, the account name and media filenames inside
``core.logs.collect_diagnostics`` — the frontend only copies the JSON.
"""
from fastapi import APIRouter, Request

from backend.core.logs import collect_diagnostics

router = APIRouter()


@router.get("/api/diagnostics")
def diagnostics(request: Request) -> dict:
    return collect_diagnostics(
        request.app.state.settings, request.app.state.store
    )
