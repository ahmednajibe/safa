"""GET /api/operations + POST /api/operations/resolve (SPEC §5)."""
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from backend.api.errors import err
from backend.core.capabilities import detect_capabilities
from backend.operations.registry import OPERATIONS, PRESETS, ops_in_order
from backend.operations.rules import ParamError, resolve, unavailable_code

router = APIRouter()


def _availability() -> dict[str, bool]:
    caps = detect_capabilities()
    return {
        op_id: unavailable_code(op, caps.modules, caps.models) is None
        for op_id, op in OPERATIONS.items()
    }


@router.get("/api/operations")
def list_operations() -> dict:
    caps = detect_capabilities()
    out = []
    for spec in ops_in_order():
        d = asdict(spec)
        reason = unavailable_code(spec, caps.modules, caps.models)
        d["available"] = reason is None
        d["unavailable_code"] = reason
        out.append(d)
    return {"operations": out, "presets": PRESETS}


class ResolveRequest(BaseModel):
    ops: dict[str, dict[str, Any]]


@router.post("/api/operations/resolve")
def resolve_ops(body: ResolveRequest) -> dict:
    try:
        eff = resolve(body.ops, _availability())
    except ParamError as exc:
        return err(422, "bad_param", str(exc), params=exc.params)
    except ValueError as exc:
        return err(422, "bad_request", str(exc))
    return {
        "ops": [{"id": op_id, "params": params} for op_id, params in eff.ops],
        "disabled": eff.disabled,
    }
