"""Task 3 tests: operation registry, presets, conflict rules, /api/operations."""
import pytest
from fastapi.testclient import TestClient

import backend.core.models_manifest as manifest
from backend.core.settings import Settings
from backend.main import create_app


def _client(tmp_path):
    return TestClient(
        create_app(Settings(data_dir=tmp_path, db_path=tmp_path / "app.db"))
    )


ALL_AVAILABLE = None  # sentinel: build inside tests from OPERATIONS


def _all_available():
    from backend.operations.registry import OPERATIONS

    return {op_id: True for op_id in OPERATIONS}


# ---- registry shape ---------------------------------------------------------


def test_fourteen_ops_in_order():
    from backend.operations.registry import OPERATIONS, ops_in_order

    assert len(OPERATIONS) == 14
    ids = [op.id for op in ops_in_order()]
    assert ids == [
        "trim", "channel", "highpass", "dehum", "separate", "dereverb",
        "denoise", "declick", "enhance", "enhance_ai", "silence",
        "speed", "loudness", "subtitles",
    ]
    orders = [op.order for op in ops_in_order()]
    assert orders == sorted(orders)


def test_names_match_ui_design():
    from backend.operations.registry import OPERATIONS

    assert OPERATIONS["trim"].name == "Trim ends"
    assert OPERATIONS["denoise"].name == "Reduce background noise"
    assert OPERATIONS["enhance"].name == "Polish voice"
    assert OPERATIONS["subtitles"].name == "Create subtitles"
    assert OPERATIONS["silence"].description == (
        "Tightens long gaps while leaving natural breathing room."
    )


def test_presets_reference_valid_ops_and_params():
    from backend.operations.registry import OPERATIONS, PRESETS
    from backend.operations.rules import validate_params

    expected = {
        "podcast", "remove_music", "echoey_room",
        "lecture", "karaoke", "jump_cut",
    }
    assert set(PRESETS) == expected
    for preset in PRESETS.values():
        assert preset["name"]
        for op_id, params in preset["ops"].items():
            assert op_id in OPERATIONS, f"{preset['name']}: unknown op {op_id}"
            validate_params(op_id, params)  # raises on bad key/value


# ---- resolve / rules --------------------------------------------------------


def test_resolve_fills_defaults_and_sorts():
    from backend.operations.rules import resolve

    eff = resolve({"loudness": {}, "highpass": {}}, _all_available())
    ids = [op_id for op_id, _ in eff.ops]
    assert ids == ["highpass", "loudness"]  # sorted by order, not click order
    params = dict(eff.ops)["loudness"]
    assert params["target"] == "podcast"
    assert params["lufs"] == -16


def test_karaoke_conflicts():
    from backend.operations.rules import resolve

    eff = resolve(
        {
            "separate": {"output": "instrumental"},
            "denoise": {},
            "enhance": {},
            "silence": {"mode": "vad"},
        },
        _all_available(),
    )
    assert [op_id for op_id, _ in eff.ops] == ["separate"]
    assert set(eff.disabled) == {"denoise", "enhance", "silence"}
    assert "karaoke" in eff.disabled["denoise"].lower()


def test_karaoke_keeps_amplitude_silence():
    from backend.operations.rules import resolve

    eff = resolve(
        {
            "separate": {"output": "instrumental"},
            "denoise": {},
            "silence": {"mode": "amplitude"},
        },
        _all_available(),
    )
    ids = [op_id for op_id, _ in eff.ops]
    assert "silence" in ids
    assert "denoise" not in ids
    assert "denoise" in eff.disabled


def test_resolve_rejects_out_of_range():
    from backend.operations.rules import resolve

    with pytest.raises(ValueError):
        resolve({"speed": {"factor": 3.0}}, _all_available())


def test_resolve_rejects_unknown_op_and_param():
    from backend.operations.rules import resolve

    with pytest.raises(ValueError):
        resolve({"bogus_op": {}}, _all_available())
    with pytest.raises(ValueError):
        resolve({"speed": {"bogus_param": 1}}, _all_available())


def test_resolve_rejects_bad_select_value():
    from backend.operations.rules import resolve

    with pytest.raises(ValueError):
        resolve({"channel": {"mode": "sideways"}}, _all_available())


def test_resolve_null_only_for_nullable_params():
    from backend.operations.rules import resolve

    # end_s is designed nullable (null = until the end)
    eff = resolve({"trim": {"end_s": None}}, _all_available())
    assert dict(eff.ops)["trim"]["end_s"] is None
    # non-nullable params reject null
    with pytest.raises(ValueError):
        resolve({"speed": {"factor": None}}, _all_available())


def test_resolve_disabled_reasons(tmp_path, monkeypatch):
    from backend.core.capabilities import refresh_capabilities
    from backend.operations.rules import resolve

    eff = resolve({"enhance_ai": {}}, {"enhance_ai": True})
    assert eff.disabled == {"enhance_ai": "Coming later"}

    # generic unavailable → machine reason
    eff = resolve({"separate": {}}, {"separate": False})
    assert eff.disabled["separate"] == "Not available on this computer"

    # model file missing → model reason (needs real detection)
    monkeypatch.setattr(manifest, "MODELS_DIR", tmp_path)
    try:
        refresh_capabilities()
        eff = resolve({"separate": {}}, {"separate": False})
    finally:
        monkeypatch.undo()
        refresh_capabilities()
    assert eff.disabled["separate"] == "Model not downloaded yet"


# ---- API --------------------------------------------------------------------


def test_operations_endpoint(tmp_path):
    r = _client(tmp_path).get("/api/operations")
    assert r.status_code == 200
    body = r.json()
    ops = body["operations"]
    assert len(ops) == 14
    assert [o["id"] for o in ops][:3] == ["trim", "channel", "highpass"]
    for op in ops:
        assert "available" in op and "unavailable_reason" in op
        assert "params" in op and "order" in op
    assert set(body["presets"]) == {
        "podcast", "remove_music", "echoey_room",
        "lecture", "karaoke", "jump_cut",
    }


def test_operations_availability_on_this_machine(tmp_path):
    """All non-planned ops available here; planned ones report 'planned'."""
    ops = {o["id"]: o for o in _client(tmp_path).get("/api/operations").json()["operations"]}
    for op_id, op in ops.items():
        if op_id in ("dereverb", "enhance_ai"):
            assert op["available"] is False
            assert op["unavailable_reason"] == "planned"
        else:
            assert op["available"] is True, op_id
            assert op["unavailable_reason"] is None


def test_model_missing_marks_unavailable(tmp_path, monkeypatch):
    """Review Focus 4: a missing model file marks only dependent ops."""
    from backend.core.capabilities import refresh_capabilities

    monkeypatch.setattr(manifest, "MODELS_DIR", tmp_path)
    try:
        refresh_capabilities()
        ops = {o["id"]: o for o in _client(tmp_path / "x").get("/api/operations").json()["operations"]}
    finally:
        monkeypatch.undo()
        refresh_capabilities()
    assert ops["separate"]["available"] is False
    assert ops["separate"]["unavailable_reason"] == "model_missing"
    assert ops["denoise"]["unavailable_reason"] == "model_missing"
    assert ops["subtitles"]["unavailable_reason"] == "model_missing"
    # silero weights are bundled in the wheel → silence is not model-gated
    assert ops["silence"]["available"] is True
    assert ops["trim"]["available"] is True


def test_resolve_endpoint_ok(tmp_path):
    r = _client(tmp_path).post(
        "/api/operations/resolve",
        json={"ops": {"loudness": {"target": "youtube"}, "highpass": {}}},
    )
    assert r.status_code == 200
    body = r.json()
    assert [o["id"] for o in body["ops"]] == ["highpass", "loudness"]
    assert body["disabled"] == {}
    assert dict((o["id"], o["params"]) for o in body["ops"])["loudness"]["target"] == "youtube"


def test_resolve_endpoint_karaoke(tmp_path):
    r = _client(tmp_path).post(
        "/api/operations/resolve",
        json={"ops": {
            "separate": {"output": "instrumental"},
            "denoise": {},
            "enhance": {},
            "silence": {"mode": "vad"},
        }},
    )
    assert r.status_code == 200
    body = r.json()
    assert [o["id"] for o in body["ops"]] == ["separate"]
    assert set(body["disabled"]) == {"denoise", "enhance", "silence"}


def test_resolve_endpoint_422(tmp_path):
    for payload in (
        {"ops": {"speed": {"factor": 3.0}}},
        {"ops": {"bogus": {}}},
        {"ops": {"speed": {"bogus": 1}}},
        {"ops": {"channel": {"mode": "sideways"}}},
    ):
        r = _client(tmp_path).post("/api/operations/resolve", json=payload)
        assert r.status_code == 422, payload
