"""API error bodies: JSON ``{"code", "message"}`` per M3a plan."""
from fastapi.responses import JSONResponse


def err(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"code": code, "message": message}, status_code=status)
