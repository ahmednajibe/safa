"""API error bodies: JSON ``{"code", "message", "params"}`` (SPEC §11)."""
from fastapi.responses import JSONResponse


def err(
    status: int, code: str, message: str, params: dict | None = None
) -> JSONResponse:
    return JSONResponse(
        {"code": code, "message": message, "params": params or {}},
        status_code=status,
    )
