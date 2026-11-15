"""M5 Task 0: localizable backend messages (SPEC §11).

Every user-facing backend message carries a snake_case ``message_code`` +
raw ``message_params`` so the frontend can translate; the English text
stays as the fallback for logs/tests. These tests pin: the ``msg()``
helper, the ``MESSAGES`` catalogue covering every code the backend emits,
the error/estimate/SSE envelopes, and the DB persistence.
"""
import json
import re
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.core.settings import Settings
from backend.main import create_app

GENERATED = Path(__file__).parent / "fixtures" / "generated"
SPEECH = GENERATED / "speech.wav"

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"

# Codes the plan/spec require; MESSAGES must hold an English template for
# each so unknown codes can never ship.
REQUIRED_CODES = {
    "silence_removed", "pauses_shortened", "no_speech_found",
    "very_quiet_raised", "gpu_fallback", "nvenc_fallback",
    "model_downloading", "job_queued", "job_done", "job_interrupted",
    "job_cancelled", "job_failed", "prep_failed", "insufficient_storage",
    "unsupported_type", "unreadable", "no_audio", "gpu_unavailable",
    "too_large", "media_not_found", "job_not_found", "bad_request",
    "bad_param", "bad_device", "bad_output", "bad_preview",
    "no_such_stream", "not_terminal", "not_retryable",
    "timeline_not_found", "in_use", "processing", "client_disconnected",
    "karaoke_speech_op", "preset_partial", "planned", "not_implemented",
    "module_missing", "model_missing", "unavailable",
}

_CODE_PATTERNS = [
    r"\bmsg\(\s*['\"]([a-z_0-9]+)['\"]",
    r"\berr\(\s*\d+\s*,\s*['\"]([a-z_0-9]+)['\"]",
    r"\b(?:message_code|error_code|warning_code|code)\s*=\s*['\"]([a-z_0-9]+)['\"]",
]


def _client(tmp_path):
    return TestClient(
        create_app(Settings(data_dir=tmp_path, db_path=tmp_path / "app.db"))
    )


def _insert_media_row(db: Path) -> None:
    con = sqlite3.connect(str(db))
    try:
        con.execute(
            "INSERT INTO media (id, filename, stored_path, size_bytes,"
            " probe_json, created_at) VALUES ('m1','x','/x',1,'{}','t')"
        )
        con.commit()
    finally:
        con.close()


def _upload(client, path: Path) -> str:
    from urllib.parse import quote

    r = client.post(
        "/api/media",
        content=path.read_bytes(),
        headers={
            "X-Filename": quote(path.name),
            "Content-Type": "application/octet-stream",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["media_id"]


# ---- msg() / MESSAGES ---------------------------------------------------------


def test_msg_returns_text_code_params():
    from backend.core.messages import msg

    text, code, params = msg("silence_removed", seconds=12.4, count=37)
    assert text == "Removed 12.4 s of silence in 37 places"
    assert code == "silence_removed"
    assert params == {"seconds": 12.4, "count": 37}


def test_msg_no_params():
    from backend.core.messages import msg

    text, code, params = msg("no_speech_found")
    assert "No speech" in text
    assert code == "no_speech_found"
    assert params == {}


def test_msg_unknown_code_raises():
    from backend.core.messages import msg

    with pytest.raises(KeyError):
        msg("totally_made_up")


def test_msg_missing_param_raises():
    from backend.core.messages import msg

    with pytest.raises(KeyError):
        msg("silence_removed", seconds=1.0)  # count missing


def test_required_codes_in_messages():
    from backend.core.messages import MESSAGES

    missing = REQUIRED_CODES - set(MESSAGES)
    assert not missing, f"codes missing from MESSAGES: {sorted(missing)}"


def test_every_code_used_in_backend_exists():
    """Grep-style: any code literal passed to msg()/err()/code kwargs must
    exist in MESSAGES — an unknown code can never reach the frontend."""
    from backend.core.messages import MESSAGES

    used: dict[str, set[str]] = {}
    for py in BACKEND_DIR.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        for pat in _CODE_PATTERNS:
            for m in re.finditer(pat, text):
                used.setdefault(m.group(1), set()).add(py.name)
    unknown = sorted(c for c in used if c not in MESSAGES)
    assert not unknown, (
        f"message codes used but not in MESSAGES: "
        f"{[(c, sorted(used[c])) for c in unknown]}"
    )


# ---- error envelope ------------------------------------------------------------


def test_err_carries_params():
    from backend.api.errors import err

    r = err(507, "insufficient_storage", "Not enough disk space.",
            params={"needed_gb": 4.2, "free_gb": 1.0})
    body = json.loads(r.body)
    assert body["code"] == "insufficient_storage"
    assert body["message"] == "Not enough disk space."
    assert body["params"] == {"needed_gb": 4.2, "free_gb": 1.0}


def test_err_params_default_empty():
    from backend.api.errors import err

    body = json.loads(err(404, "media_not_found", "gone").body)
    assert body["params"] == {}


# ---- estimate bucket_code --------------------------------------------------------


def test_estimate_returns_bucket_code(tmp_path):
    from backend.db import connect

    c = _client(tmp_path)
    con = connect(tmp_path / "app.db")
    try:
        con.execute(
            "INSERT INTO media (id, filename, stored_path, size_bytes,"
            " probe_json, created_at) VALUES (?,?,?,?,?,?)",
            ("m1", "x.wav", "/x.wav", 1,
             json.dumps({"duration_s": 600.0, "audio": [], "video": []}),
             "2026-11-15T00:00:00"),
        )
        con.commit()
    finally:
        con.close()
    r = c.get(
        "/api/estimate",
        params={
            "media_id": "m1", "device": "cpu",
            "ops": json.dumps([{"id": "subtitles", "params": {}}]),
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["bucket_code"] in ("under_1m", "about_n_m", "over_30m")
    assert "minutes" in body
    if body["bucket_code"] == "about_n_m":
        assert isinstance(body["minutes"], int) and body["minutes"] >= 2
    else:
        assert body["minutes"] is None


def test_bucket_code_boundaries():
    from backend.pipeline.estimates import bucket_code_for

    assert bucket_code_for(30) == ("under_1m", None)
    assert bucket_code_for(59.9) == ("under_1m", None)
    code, minutes = bucket_code_for(150)
    assert code == "about_n_m" and minutes == 3
    assert bucket_code_for(2000) == ("over_30m", None)


# ---- operations: unavailable_code + conflict envelope ----------------------------


def test_operations_carry_unavailable_code(tmp_path):
    ops = _client(tmp_path).get("/api/operations").json()["operations"]
    for op in ops:
        assert "unavailable_code" in op
    by_id = {o["id"]: o for o in ops}
    assert by_id["dereverb"]["unavailable_code"] == "planned"
    assert by_id["enhance_ai"]["unavailable_code"] == "planned"
    assert by_id["silence"]["unavailable_code"] is None


def test_resolve_conflict_carries_code_and_blocker():
    from backend.operations.registry import OPERATIONS
    from backend.operations.rules import resolve

    eff = resolve(
        {
            "separate": {"output": "instrumental"},
            "denoise": {},
            "silence": {"mode": "vad"},
        },
        {op_id: True for op_id in OPERATIONS},
        set(OPERATIONS),
    )
    denoise = eff.disabled["denoise"]
    assert denoise["code"] == "karaoke_speech_op"
    assert denoise["blocker_op_id"] == "separate"
    assert "karaoke" in denoise["message"].lower()


def test_resolve_disabled_reason_codes():
    from backend.operations.rules import resolve

    eff = resolve({"enhance_ai": {}}, {"enhance_ai": True}, {"enhance_ai"})
    assert eff.disabled["enhance_ai"]["code"] == "planned"
    assert eff.disabled["enhance_ai"]["blocker_op_id"] is None

    eff = resolve({"separate": {}}, {"separate": True}, set())
    assert eff.disabled["separate"]["code"] == "not_implemented"


# ---- job payload / SSE ---------------------------------------------------------


def test_job_created_with_queued_message_code(tmp_path):
    c = _client(tmp_path)
    mid = _upload(c, SPEECH)
    r = c.post("/api/jobs", json={"media_id": mid, "device": "cpu"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["message_code"] == "job_queued"
    assert body["message_params"]["position"] >= 1


def test_sse_state_carries_message_code(tmp_path):
    c = _client(tmp_path)
    mid = _upload(c, SPEECH)
    jid = c.post(
        "/api/jobs", json={"media_id": mid, "device": "cpu"}
    ).json()["id"]
    with c.stream("GET", f"/api/jobs/{jid}/events") as r:
        it = r.iter_lines()
        assert next(it) == "event: state"
        payload = json.loads(next(it).split(":", 1)[1].strip())
    assert "message_code" in payload
    assert "message_params" in payload
    assert "warnings" in payload


def test_disabled_ops_reported_with_codes(tmp_path):
    c = _client(tmp_path)
    mid = _upload(c, SPEECH)
    r = c.post("/api/jobs", json={
        "media_id": mid, "device": "cpu", "ops": {"dereverb": {}},
    })
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["disabled"]["dereverb"]["code"] == "planned"
    # dropped ops also recorded as a job warning
    assert any(
        w["warning_code"] == "preset_partial" for w in body["warnings"]
    )


# ---- store / DB persistence ------------------------------------------------------


def test_progress_message_code_persisted(tmp_path):
    from backend.db import init_db
    from backend.jobs.store import JobStore
    from backend.pipeline.types import Progress

    db = tmp_path / "app.db"
    init_db(db)
    _insert_media_row(db)
    store = JobStore(db)
    job = store.create("m1", "full", [], {}, None, "cpu", 0)
    store.update_progress(
        job.id,
        Progress(
            stage="silence", stage_idx=1, total_stages=3,
            stage_pct=0.5, overall_pct=0.4,
            message="Removed 1.0 s of silence in 2 places",
            message_code="silence_removed",
            message_params={"seconds": 1.0, "count": 2},
        ),
    )
    got = store.get(job.id)
    assert got.message_code == "silence_removed"
    assert got.message_params == {"seconds": 1.0, "count": 2}


def test_finish_and_warnings_persisted(tmp_path):
    from backend.db import init_db
    from backend.jobs.store import JobStore

    db = tmp_path / "app.db"
    init_db(db)
    _insert_media_row(db)
    store = JobStore(db)
    job = store.create("m1", "full", [], {}, None, "cpu", 0)
    store.add_warning(job.id, "gpu_fallback", {"op_id": "denoise"})
    store.add_warning(job.id, "preset_partial",
                      {"skipped_op_ids": ["denoise"]})
    got = store.get(job.id)
    assert got.warnings == [
        {"warning_code": "gpu_fallback",
         "warning_params": {"op_id": "denoise"}},
        {"warning_code": "preset_partial",
         "warning_params": {"skipped_op_ids": ["denoise"]}},
    ]
    store.finish(
        job.id, "failed", error="boom",
        error_code="job_failed", error_params={"op_id": "silence"},
    )
    got = store.get(job.id)
    assert got.error_code == "job_failed"
    assert got.error_params == {"op_id": "silence"}


def test_migration_adds_message_columns(tmp_path):
    """M2-era DB gains the M5 message columns; migration is idempotent."""
    from backend.db import init_db

    db = tmp_path / "old.db"
    con = sqlite3.connect(str(db))
    con.execute(
        "CREATE TABLE media (id TEXT PRIMARY KEY, filename TEXT,"
        " stored_path TEXT, size_bytes INTEGER, probe_json TEXT,"
        " created_at TEXT)"
    )
    con.execute(
        "CREATE TABLE jobs (id TEXT PRIMARY KEY, media_id TEXT, kind TEXT,"
        " ops_json TEXT, output_json TEXT, preview_json TEXT, state TEXT,"
        " stage TEXT, stage_idx INTEGER, total_stages INTEGER,"
        " stage_pct REAL, overall_pct REAL, eta_sec REAL, device TEXT,"
        " message TEXT, error TEXT, worker_pid INTEGER, created_at TEXT,"
        " started_at TEXT, finished_at TEXT)"
    )
    con.execute(
        "INSERT INTO jobs (id, media_id, kind, state) VALUES"
        " ('j1','m1','full','queued')"
    )
    con.commit()
    con.close()
    init_db(db)
    init_db(db)  # second run must not raise
    con = sqlite3.connect(str(db))
    cols = {r[1] for r in con.execute("PRAGMA table_info(jobs)")}
    con.close()
    assert {
        "message_code", "message_params_json",
        "error_code", "error_params_json", "warnings_json",
    } <= cols
