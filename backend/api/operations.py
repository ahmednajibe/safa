"""GET /api/operations + POST /api/operations/resolve (SPEC §5)."""
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from backend.api.errors import err
from backend.core.capabilities import detect_capabilities
from backend.core.messages import MESSAGES
from backend.operations.registry import OPERATIONS, PRESETS, ops_in_order
from backend.operations.rules import (
    ModelMissingError, ParamError, resolve, unavailable_code,
)

router = APIRouter()


def _availability() -> dict[str, bool]:
    caps = detect_capabilities()
    return {
        op_id: unavailable_code(op, caps.modules, caps.models) is None
        for op_id, op in OPERATIONS.items()
    }


def _download_hint(spec, models: dict[str, bool]) -> str | None:
    """M10 T3: the manifest model id an op card's Download button should
    fetch — the first missing required model, else the model behind the
    default choice (the card is only ``model_missing`` when every choice
    is missing, so any hint is actionable)."""
    for m in spec.requires_models:
        if not models.get(m, False):
            return m
    for key, choice_map in spec.model_options.items():
        default = next(
            (p.default for p in spec.params if p.key == key), None
        )
        mid = choice_map.get(default)
        if mid is not None and not models.get(mid, False):
            return mid
        for alt in choice_map.values():
            if not models.get(alt, False):
                return alt
    return None


@router.get("/api/operations")
def list_operations() -> dict:
    caps = detect_capabilities()
    out = []
    for spec in ops_in_order():
        if spec.hidden:
            continue  # M4d: pipeline-internal stages get no card
        d = asdict(spec)
        reason = unavailable_code(spec, caps.modules, caps.models)
        d["available"] = reason is None
        d["unavailable_code"] = reason
        hint = (
            _download_hint(spec, caps.models)
            if reason == "model_missing"
            else None
        )
        d["unavailable_model"] = hint
        if hint is not None:
            from backend.core.models_manifest import MODELS

            d["unavailable_model_mb"] = MODELS[hint].approx_mb
        else:
            d["unavailable_model_mb"] = None
        out.append(d)
    return {"operations": out, "presets": PRESETS}


class ResolveRequest(BaseModel):
    ops: dict[str, dict[str, Any]]


@router.post("/api/operations/resolve")
def resolve_ops(body: ResolveRequest) -> dict:
    caps = detect_capabilities()
    try:
        eff = resolve(body.ops, _availability(), models=caps.models)
    except ModelMissingError as exc:
        return err(
            422, "model_missing", MESSAGES["model_missing"],
            params=exc.params,
        )
    except ParamError as exc:
        return err(422, "bad_param", str(exc), params=exc.params)
    except ValueError as exc:
        return err(422, "bad_request", str(exc))
    return {
        "ops": [{"id": op_id, "params": params} for op_id, params in eff.ops],
        "disabled": eff.disabled,
    }
