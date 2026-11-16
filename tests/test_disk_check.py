"""M10 T5: pre-queue disk checks + mid-run ENOSPC → ``disk_full``.

``POST /api/jobs`` and ``POST /api/jobs/{id}/retry`` must estimate the
peak footprint (48 kHz float32 intermediates × ≥2 + outputs + video
re-encode) and refuse with 507 ``insufficient_storage`` before a job is
queued; a disk that fills mid-run must become a clear ``disk_full``
failure with ``work/`` cleaned.
"""
import errno
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import pytest

from backend.core.settings import Settings
from backend.db import connect, init_db
from backend.jobs.store import JobStore
from backend.main import create_app
from backend.media.probe import probe
from backend.media.storage import job_dir
from backend.operations.impl import IMPLEMENTATIONS
from backend.worker import main as worker_main
from tests.client import make_client

GENERATED = Path(__file__).parent / "fixtures" / "generated"
SPEECH = GENERATED / "speech.wav"


def _client(tmp_path):
    return make_client(
        create_app(
            Settings(data_dir=tmp_path, db_path=tmp_path / "app.db")
        )
    )


def _upload(client, path: Path) -> str:
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


# ---- the estimator -------------------------------------------------------------


def test_estimate_peak_bytes_formula():
    from backend.core.disk import estimate_peak_bytes

    pcm = 3600 * 48000 * 4 * 2  # one 48 kHz float32 stereo hour ≈ 1.38 GB
    b = estimate_peak_bytes(
        duration_s=3600.0, channels=2, has_video=False,
        source_size_bytes=50_000_000,
    )
    # ≥ 2 intermediates + an output buffer
    assert b >= 3 * pcm
    # kept video adds the re-encode worst case (source + audio buffer)
    bv = estimate_peak_bytes(
        duration_s=3600.0, channels=2, has_video=True,
        source_size_bytes=50_000_000,
    )
    assert bv >= b + 50_000_000


# ---- API-side 507 ---------------------------------------------------------------


def test_job_disk_check(tmp_path, monkeypatch):
    c = _client(tmp_path)
    mid = _upload(c, SPEECH)

    r = c.post("/api/jobs", json={"media_id": mid, "device": "cpu"})
    assert r.status_code == 201, r.text

    import backend.api.jobs as jobs_api

    monkeypatch.setattr(jobs_api, "free_space_bytes", lambda p: 1024)
    r = c.post("/api/jobs", json={"media_id": mid, "device": "cpu"})
    assert r.status_code == 507
    body = r.json()
    assert body["code"] == "insufficient_storage"
    assert body["params"]["needed_gb"] > 0
    assert "free_gb" in body["params"]
    # The rejected submission queued nothing (only job #1 exists).
    assert len(c.get("/api/jobs").json()["jobs"]) == 1


def test_retry_disk_check(tmp_path, monkeypatch):
    c = _client(tmp_path)
    mid = _upload(c, SPEECH)
    jid = c.post(
        "/api/jobs", json={"media_id": mid, "device": "cpu"}
    ).json()["id"]
    store = JobStore(tmp_path / "app.db")
    store.finish(jid, "failed", error="x", error_code="job_failed")

    import backend.api.jobs as jobs_api

    monkeypatch.setattr(jobs_api, "free_space_bytes", lambda p: 1024)
    r = c.post(f"/api/jobs/{jid}/retry")
    assert r.status_code == 507
    assert r.json()["code"] == "insufficient_storage"


# ---- worker ENOSPC → disk_full ----------------------------------------------------


def test_worker_enospc_disk_full(tmp_path, monkeypatch):
    db = tmp_path / "app.db"
    data = tmp_path / "data"
    data.mkdir()
    init_db(db)
    monkeypatch.setenv("SOUND_SYSTEM_DB", str(db))
    monkeypatch.setenv("SOUND_SYSTEM_DATA", str(data))

    con = connect(db)
    try:
        con.execute(
            "INSERT INTO media (id, filename, stored_path, size_bytes,"
            " probe_json, created_at) VALUES (?,?,?,?,?,?)",
            (
                "m1", SPEECH.name, str(SPEECH), SPEECH.stat().st_size,
                json.dumps(probe(SPEECH).to_dict()),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        con.commit()
    finally:
        con.close()

    store = JobStore(db)
    j = store.create(
        media_id="m1", kind="full", ops=[("loudness", {})],
        output={"audio_format": "wav", "keep_video": False},
        preview=None, device="cpu", audio_stream=0,
    )
    job_root = job_dir(j.id, data)
    (job_root / "work" / "intermediate.wav").write_bytes(b"x" * 64)

    def _full(*a, **kw):
        raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))

    monkeypatch.setitem(IMPLEMENTATIONS, "loudness", _full)
    rc = worker_main([j.id])

    assert rc == 1
    got = store.get(j.id)
    assert got.state == "failed"
    assert got.error_code == "disk_full"
    work = job_root / "work"
    assert not work.exists() or not any(work.iterdir())
