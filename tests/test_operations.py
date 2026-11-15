"""Task 3 tests: operation registry, presets, conflict rules, /api/operations."""
from pathlib import Path

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


def _all_implemented():
    """Every op id — exercises pure rule logic regardless of which
    milestones have landed (``resolve(implemented=...)``)."""
    from backend.operations.registry import OPERATIONS

    return set(OPERATIONS)


# ---- registry shape ---------------------------------------------------------


def test_fifteen_ops_in_order():
    from backend.operations.registry import OPERATIONS, ops_in_order

    assert len(OPERATIONS) == 15
    ids = [op.id for op in ops_in_order()]
    assert ids == [
        "trim", "channel", "highpass", "dehum", "separate", "declick",
        "denoise", "dereverb", "breaths", "enhance", "enhance_ai",
        "silence", "speed", "loudness", "subtitles",
    ]
    orders = [op.order for op in ops_in_order()]
    assert orders == sorted(orders)
    assert OPERATIONS["declick"].order == 38  # between dehum and denoise (M4c)
    assert OPERATIONS["dereverb"].order == 42  # after denoise, before enhance (M6)
    assert OPERATIONS["breaths"].order == 46  # after dereverb, before enhance (M6b)
    assert OPERATIONS["highpass"].params[0].default == 90


def test_names_match_ui_design():
    from backend.operations.registry import OPERATIONS

    assert OPERATIONS["trim"].name == "Trim ends"
    assert OPERATIONS["denoise"].name == "Reduce background noise"
    assert OPERATIONS["enhance"].name == "Polish voice"
    assert OPERATIONS["subtitles"].name == "Create subtitles"
    assert OPERATIONS["silence"].name == "Cut silence"
    assert OPERATIONS["silence"].description == (
        "Removes the silent parts so only the speech is left"
        " (or just shortens long pauses)."
    )


def test_silence_params_match_spec10():
    """M5 Task 1: SPEC §10 — action remove by default, exact keys/ranges."""
    from backend.operations.registry import OPERATIONS

    params = {p.key: p for p in OPERATIONS["silence"].params}
    assert params["action"].default == "remove"
    assert {o["value"] for o in params["action"].options} == {
        "remove", "shorten"
    }
    assert params["min_silence_s"].default == 0.3
    assert params["min_silence_s"].min == 0.1
    assert params["min_silence_s"].max == 3.0
    assert params["pad_s"].default == 0.1
    assert params["pad_s"].min == 0
    assert params["pad_s"].max == 0.5
    assert params["keep_pause_s"].default == 0.25
    assert params["mode"].default == "vad"
    assert {o["value"] for o in params["mode"].options} == {
        "vad", "amplitude"
    }
    assert params["threshold_db"].default == -40
    # the M2-era max_pause_s semantics are gone (SPEC §10 overrides)
    assert "max_pause_s" not in params


def test_silence_presets_use_remove():
    from backend.operations.registry import PRESETS

    for key in ("podcast", "lecture", "jump_cut"):
        assert PRESETS[key]["ops"]["silence"] == {"action": "remove"}, key


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
        _all_implemented(),
    )
    assert [op_id for op_id, _ in eff.ops] == ["separate"]
    assert set(eff.disabled) == {"denoise", "enhance", "silence"}
    entry = eff.disabled["denoise"]
    assert entry["code"] == "karaoke_speech_op"
    assert entry["blocker_op_id"] == "separate"
    assert "karaoke" in entry["message"].lower()


def test_karaoke_keeps_amplitude_silence():
    from backend.operations.rules import resolve

    eff = resolve(
        {
            "separate": {"output": "instrumental"},
            "denoise": {},
            "silence": {"mode": "amplitude"},
        },
        _all_available(),
        _all_implemented(),
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
    assert eff.disabled["enhance_ai"]["code"] == "planned"
    assert eff.disabled["enhance_ai"]["message"] == "Coming later"

    # generic unavailable → machine reason
    eff = resolve({"separate": {}}, {"separate": False})
    assert eff.disabled["separate"]["message"] == (
        "Not available on this computer"
    )

    # model file missing → model reason (needs real detection)
    monkeypatch.setattr(manifest, "MODELS_DIR", tmp_path)
    try:
        refresh_capabilities()
        eff = resolve({"separate": {}}, {"separate": False})
    finally:
        monkeypatch.undo()
        refresh_capabilities()
    entry = eff.disabled["separate"]
    assert entry["code"] == "model_missing"
    assert entry["message"] == "Model not downloaded yet"


def test_resolve_disables_unimplemented_ops():
    """M4c Task 3: an op that is *available* (modules/models present) but has
    no runner implementation must not reach the pipeline — it goes to
    ``disabled`` with "Coming in a later update" instead of raising
    NotImplementedError. (M5: silence is implemented and no longer drops.)"""
    from backend.operations.impl import IMPLEMENTATIONS
    from backend.operations.registry import OPERATIONS, PRESETS
    from backend.operations.rules import resolve

    eff = resolve(PRESETS["podcast"]["ops"], _all_available())
    ids = {op_id for op_id, _ in eff.ops}
    assert ids <= set(IMPLEMENTATIONS)
    assert "denoise" in ids  # implemented in M6
    assert "dereverb" in ids  # implemented in M6
    assert "silence" in ids  # implemented in M5
    # the runnable part keeps the new effective order
    assert [op_id for op_id, _ in eff.ops] == [
        "highpass", "dehum", "declick", "denoise", "dereverb",
        "breaths", "enhance", "silence", "loudness"
    ]
    # explicit opt-in to an unimplemented op gets the same graceful path
    eff2 = resolve({"separate": {}, "loudness": {}}, _all_available(),
                   {"loudness"})
    assert [op_id for op_id, _ in eff2.ops] == ["loudness"]
    assert eff2.disabled["separate"]["code"] == "not_implemented"
    # planned ops keep their own reason
    eff3 = resolve({"enhance_ai": {}}, _all_available(), {"enhance_ai"})
    assert eff3.disabled["enhance_ai"]["code"] == "planned"


# ---- API --------------------------------------------------------------------


def test_operations_endpoint(tmp_path):
    r = _client(tmp_path).get("/api/operations")
    assert r.status_code == 200
    body = r.json()
    ops = body["operations"]
    assert len(ops) == 15
    assert [o["id"] for o in ops][:3] == ["trim", "channel", "highpass"]
    for op in ops:
        assert "available" in op and "unavailable_code" in op
        assert "params" in op and "order" in op
    assert set(body["presets"]) == {
        "podcast", "remove_music", "echoey_room",
        "lecture", "karaoke", "jump_cut",
    }


def test_operations_availability_on_this_machine(tmp_path):
    """All non-planned ops available here; planned ones report 'planned'."""
    ops = {o["id"]: o for o in _client(tmp_path).get("/api/operations").json()["operations"]}
    for op_id, op in ops.items():
        if op_id in ("enhance_ai",):
            assert op["available"] is False
            assert op["unavailable_code"] == "planned"
        else:
            assert op["available"] is True, op_id
            assert op["unavailable_code"] is None


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
    assert ops["separate"]["unavailable_code"] == "model_missing"
    assert ops["denoise"]["unavailable_code"] == "model_missing"
    assert ops["subtitles"]["unavailable_code"] == "model_missing"
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
    # M7: separate is implemented — the karaoke conflict disables every
    # speech-centric op selected with it (denoise/enhance/silence-vad).
    assert [o["id"] for o in body["ops"]] == ["separate"]
    assert set(body["disabled"]) == {"denoise", "enhance", "silence"}
    assert body["disabled"]["denoise"]["code"] == "karaoke_speech_op"


def test_resolve_endpoint_422(tmp_path):
    for payload in (
        {"ops": {"speed": {"factor": 3.0}}},
        {"ops": {"bogus": {}}},
        {"ops": {"speed": {"bogus": 1}}},
        {"ops": {"channel": {"mode": "sideways"}}},
    ):
        r = _client(tmp_path).post("/api/operations/resolve", json=payload)
        assert r.status_code == 422, payload


# ---- R1 B10: per-selected-model gating -----------------------------------------


def test_unavailable_code_separate_per_model():
    """R1 B10: the separate card is unavailable only when NEITHER model
    is downloaded — one present model keeps it selectable."""
    from backend.operations.registry import OPERATIONS
    from backend.operations.rules import unavailable_code

    spec = OPERATIONS["separate"]
    mods = {"audio_separator": True}
    for models, expected in (
        ({"htdemucs": True, "kim_vocal_2": True}, None),
        ({"htdemucs": True, "kim_vocal_2": False}, None),
        ({"htdemucs": False, "kim_vocal_2": True}, None),
        ({"htdemucs": False, "kim_vocal_2": False}, "model_missing"),
    ):
        assert unavailable_code(spec, mods, models) == expected, models


def test_unavailable_code_subtitles_per_model():
    """R1 B10: subtitles stay selectable while *either* whisper model is
    present; both missing → model_missing."""
    from backend.operations.registry import OPERATIONS
    from backend.operations.rules import unavailable_code

    spec = OPERATIONS["subtitles"]
    mods = {"faster_whisper": True}
    for models, expected in (
        ({"whisper_small": True, "whisper_turbo": False}, None),
        ({"whisper_small": False, "whisper_turbo": True}, None),
        ({"whisper_small": False, "whisper_turbo": False},
         "model_missing"),
    ):
        assert unavailable_code(spec, mods, models) == expected, models


def test_resolve_rejects_missing_selected_model():
    """R1 B10: choosing a model that isn't downloaded raises
    ModelMissingError naming the model id."""
    from backend.operations.rules import ModelMissingError, resolve

    avail = _all_available()
    impl = _all_implemented()
    sep = {"htdemucs": True, "kim_vocal_2": False}
    with pytest.raises(ModelMissingError) as ei:
        resolve({"separate": {"model": "kim_vocal_2"}},
                avail, impl, models=sep)
    assert ei.value.params["model_id"] == "kim_vocal_2"
    eff = resolve({"separate": {"model": "htdemucs"}},
                  avail, impl, models=sep)
    assert [o for o, _ in eff.ops] == ["separate"]

    wh = {"whisper_small": True, "whisper_turbo": False}
    with pytest.raises(ModelMissingError) as ei:
        resolve({"subtitles": {"model": "accurate"}},
                avail, impl, models=wh)
    assert ei.value.params["model_id"] == "whisper_turbo"
    eff = resolve({"subtitles": {"model": "fast"}},
                  avail, impl, models=wh)
    assert [o for o, _ in eff.ops] == ["subtitles"]


def test_resolve_no_models_arg_skips_model_gate():
    """models=None keeps the old behaviour (estimates never fail)."""
    from backend.operations.rules import resolve

    eff = resolve(
        {"separate": {"model": "kim_vocal_2"}},
        _all_available(), _all_implemented(),
    )
    assert [o for o, _ in eff.ops] == ["separate"]


def test_job_and_resolve_api_422_model_missing(tmp_path, monkeypatch):
    """A missing *selected* model is a 422 model_missing{model_id} —
    the job never silently switches engines."""
    from dataclasses import replace
    from urllib.parse import quote

    from backend.core.capabilities import detect_capabilities
    import backend.api.jobs as jobs_api
    import backend.api.operations as ops_api

    caps = detect_capabilities()
    fake = replace(
        caps,
        modules={
            **caps.modules,
            "audio_separator": True, "faster_whisper": True,
        },
        models={
            **caps.models,
            "htdemucs": True, "kim_vocal_2": False,
            "whisper_small": True, "whisper_turbo": False,
        },
    )
    monkeypatch.setattr(jobs_api, "detect_capabilities", lambda: fake)
    monkeypatch.setattr(ops_api, "detect_capabilities", lambda: fake)

    c = _client(tmp_path)
    clip = Path(__file__).parent / "fixtures" / "generated" / "speech.wav"
    r = c.post(
        "/api/media",
        content=clip.read_bytes(),
        headers={
            "X-Filename": quote(clip.name),
            "Content-Type": "application/octet-stream",
        },
    )
    assert r.status_code == 201, r.text
    mid = r.json()["media_id"]

    r = c.post("/api/jobs", json={
        "media_id": mid, "device": "cpu",
        "ops": {"separate": {"model": "kim_vocal_2"}},
    })
    assert r.status_code == 422, r.text
    body = r.json()
    assert body["code"] == "model_missing"
    assert body["params"]["model_id"] == "kim_vocal_2"

    r = c.post("/api/jobs", json={
        "media_id": mid, "device": "cpu",
        "ops": {"subtitles": {"model": "accurate"}},
    })
    assert r.status_code == 422
    assert r.json()["params"]["model_id"] == "whisper_turbo"

    r = c.post("/api/operations/resolve", json={
        "ops": {"separate": {"model": "kim_vocal_2"}},
    })
    assert r.status_code == 422
    assert r.json()["code"] == "model_missing"
