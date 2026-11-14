"""Conflict + availability rules for the operation registry (SPEC §2).

``resolve()`` turns a user's selection into the effective ordered op list and
a ``disabled`` map with human-readable reasons. Pure logic — no processing.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.operations.registry import OPERATIONS, OpSpec, ParamSpec

# UI_DESIGN §6: conflict wording — one human sentence per op.
KARAOKE_DISABLED_REASONS = {
    "denoise": "Off while Karaoke is on: noise reduction works on speech.",
    "enhance": "Off while Karaoke is on: voice polish works on speech.",
    "silence": "Off while Karaoke is on: speech detection needs a voice.",
}

REASON_NOT_AVAILABLE = "Not available on this computer"
REASON_MODEL_MISSING = "Model not downloaded yet"
REASON_PLANNED = "Coming later"


@dataclass
class Effective:
    ops: list[tuple[str, dict]]
    disabled: dict[str, str]


def validate_params(op_id: str, params: dict[str, Any]) -> dict[str, Any]:
    """Validate user params against the op's schema; return defaults merged
    with the (validated) overrides. Raises ValueError on bad input."""
    spec = OPERATIONS.get(op_id)
    if spec is None:
        raise ValueError(f"Unknown operation: {op_id}")
    schema = {p.key: p for p in spec.params}
    merged: dict[str, Any] = {p.key: p.default for p in spec.params}
    for key, value in (params or {}).items():
        p = schema.get(key)
        if p is None:
            raise ValueError(f"{spec.name}: unknown setting '{key}'")
        merged[key] = _validate_value(spec, p, value)
    return merged


def _validate_value(spec: OpSpec, p: ParamSpec, value: Any) -> Any:
    if value is None:
        # Only params designed nullable (default None) accept empty values,
        # e.g. trim end_s = until the end.
        if p.default is None:
            return None
        raise ValueError(f"{spec.name}: '{p.label}' needs a value")
    if p.type == "bool":
        if not isinstance(value, bool):
            raise ValueError(f"{spec.name}: '{p.label}' must be on or off")
        return value
    if p.type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{spec.name}: '{p.label}' must be a number")
        v = float(value)
        if p.min is not None and v < p.min:
            raise ValueError(
                f"{spec.name}: '{p.label}' must be at least {p.min}"
            )
        if p.max is not None and v > p.max:
            raise ValueError(
                f"{spec.name}: '{p.label}' must be at most {p.max}"
            )
        return value
    if p.type == "select":
        allowed = {o["value"] for o in (p.options or [])}
        if value not in allowed:
            raise ValueError(
                f"{spec.name}: '{p.label}' must be one of "
                + ", ".join(repr(o["value"]) for o in (p.options or []))
            )
        return value
    raise ValueError(f"{spec.name}: bad schema for '{p.key}'")


def unavailable_code(
    op: OpSpec, modules: dict[str, bool], models: dict[str, bool]
) -> str | None:
    """Machine reason an op can't be selected, or None if available."""
    if op.planned:
        return "planned"
    if any(not modules.get(m, False) for m in op.requires_modules):
        return "module_missing"
    if any(not models.get(m, False) for m in op.requires_models):
        return "model_missing"
    return None


def _unavailable_reason(op: OpSpec) -> str:
    """Human sentence for an op the caller marked unavailable."""
    from backend.core.capabilities import detect_capabilities

    caps = detect_capabilities()
    if unavailable_code(op, caps.modules, caps.models) == "model_missing":
        return REASON_MODEL_MISSING
    return REASON_NOT_AVAILABLE


def resolve(selection: dict[str, dict], available: dict[str, bool]) -> Effective:
    """Validate + order a selection and apply conflict/availability rules."""
    disabled: dict[str, str] = {}
    merged: dict[str, dict] = {}
    for op_id, params in selection.items():
        spec = OPERATIONS.get(op_id)
        if spec is None:
            raise ValueError(f"Unknown operation: {op_id}")
        if spec.planned:
            disabled[op_id] = REASON_PLANNED
            continue
        if not available.get(op_id, True):
            disabled[op_id] = _unavailable_reason(spec)
            continue
        merged[op_id] = validate_params(op_id, params)

    # SPEC §2: karaoke (separate output=instrumental) disables speech-based ops.
    if merged.get("separate", {}).get("output") == "instrumental":
        for op_id, reason in KARAOKE_DISABLED_REASONS.items():
            if op_id not in merged:
                continue
            if op_id == "silence" and merged[op_id].get("mode") != "vad":
                continue  # amplitude mode doesn't need speech
            del merged[op_id]
            disabled[op_id] = reason

    ordered = sorted(merged, key=lambda op_id: OPERATIONS[op_id].order)
    return Effective(ops=[(op_id, merged[op_id]) for op_id in ordered], disabled=disabled)
