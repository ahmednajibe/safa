"""M10 T6: retention sweep.

Terminal jobs older than ``retention_days`` (by ``finished_at``) lose
their folders + rows; media referenced by no remaining job follows.
Queued/running jobs, kept jobs, active preparations, outside paths and
junction/symlink escapes are never touched.
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import pytest

from backend.core.retention import _rmtree_safe, retention_days, sweep
from backend.core.settings import Settings
from backend.db import connect, init_db
from backend.jobs.models import Job
from backend.jobs.store import JobStore
from backend.main import create_app
from backend.media.storage import job_dir, media_dir
from tests.client import make_client

GENERATED = Path(__file__).parent / "fixtures" / "generated"
SPEECH = GENERATED / "speech.wav"

TERMINAL = {"completed", "failed", "cancelled", "interrupted"}


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _media_row(db: Path, mid: str, stored: Path,
               created_at: str = "2026-01-01T00:00:00+00:00"):
    con = connect(db)
    try:
        con.execute(
            "INSERT INTO media (id, filename, stored_path, size_bytes,"
            " probe_json, created_at) VALUES (?,?,?,?,?,?)",
            (mid, stored.name, str(stored), stored.stat().st_size,
             "{}", created_at),
        )
        con.commit()
    finally:
        con.close()


def _job(store: JobStore, mid: str, *, state: str, keep: bool = False,
         finished_at: datetime | None = None, created=None) -> Job:
    j = store.create(
        media_id=mid, kind="full", ops=[], output={}, preview=None,
        device="cpu", audio_stream=0,
    )
    con = connect(store.db_path)
    try:
        con.execute(
            "UPDATE jobs SET state=?, finished_at=?, created_at=COALESCE(?,created_at)"
            " WHERE id=?",
            (
                state,
                _iso(finished_at) if finished_at else None,
                _iso(created) if created else None,
                j.id,
            ),
        )
        con.commit()
    finally:
        con.close()
    if keep:
        store.set_keep(j.id, True)
    return store.get(j.id)


def _setup(tmp_path):
    data = tmp_path / "data"
    db = tmp_path / "app.db"
    init_db(db)
    store = JobStore(db)
    return data, db, store


def test_retention_cleanup(tmp_path):
    data, db, store = _setup(tmp_path)
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=10)

    _media_row(db, "m1", SPEECH)
    mdir = media_dir("m1", data)
    mdir.mkdir(parents=True)
    (mdir / "original.wav").write_bytes(b"x")
    (mdir / "source.wav").write_bytes(b"y")

    # expired terminal job with files on disk
    j_old = _job(store, "m1", state="completed", finished_at=old)
    jdir = job_dir(j_old.id, data)
    (jdir / "output" / "clean.wav").write_bytes(b"o" * 64)

    # recent + queued + running jobs must survive
    _media_row(db, "m2", SPEECH)
    j_new = _job(store, "m2", state="completed", finished_at=now)
    j_run = _job(store, "m2", state="running", finished_at=None)
    j_q = _job(store, "m2", state="queued", finished_at=None)
    media_dir("m2", data).mkdir(parents=True)

    stats = sweep(store, data, 7, now=now)

    assert stats["jobs"] == 1
    assert stats["media"] == 1
    assert store.get(j_old.id) is None
    assert not jdir.exists()
    assert not mdir.exists()
    assert store.get(j_new.id) is not None
    assert store.get(j_run.id) is not None
    assert store.get(j_q.id) is not None
    assert media_dir("m2", data).exists()
    con = connect(db)
    try:
        assert con.execute(
            "SELECT COUNT(*) FROM media WHERE id='m1'"
        ).fetchone()[0] == 0
        assert con.execute(
            "SELECT COUNT(*) FROM media WHERE id='m2'"
        ).fetchone()[0] == 1
    finally:
        con.close()


def test_keep_flag_survives(tmp_path):
    data, db, store = _setup(tmp_path)
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=30)
    _media_row(db, "m1", SPEECH)
    j_keep = _job(store, "m1", state="completed", finished_at=old, keep=True)
    jdir = job_dir(j_keep.id, data)
    media_dir("m1", data).mkdir(parents=True)

    stats = sweep(store, data, 7, now=now)
    assert stats["jobs"] == 0 and stats["media"] == 0
    assert store.get(j_keep.id) is not None
    assert jdir.exists() and media_dir("m1", data).exists()


def test_keep_set_after_listing_survives_sweep(tmp_path):
    """R2 #14: the user hits Keep between the expired listing and the
    per-job re-check — ``_expired``'s ``or job.keep`` is the guard the
    SQL-level keep filter cannot provide. Row AND files must survive."""
    data, db, store = _setup(tmp_path)
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=30)
    _media_row(db, "m1", SPEECH)
    j = _job(store, "m1", state="completed", finished_at=old)
    jdir = job_dir(j.id, data)
    out = jdir / "output" / "clean.wav"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(b"o" * 64)
    media_dir("m1", data).mkdir(parents=True)

    real_list = store.expired_terminal_jobs

    def _flip(cutoff_iso):
        jobs = real_list(cutoff_iso)   # unkept job is listed…
        store.set_keep(j.id, True)     # …then Keep lands before re-check
        return jobs

    store.expired_terminal_jobs = _flip  # type: ignore[method-assign]
    try:
        stats = sweep(store, data, 7, now=now)
    finally:
        store.expired_terminal_jobs = real_list

    assert stats["jobs"] == 0
    assert store.get(j.id) is not None   # DB row survives
    assert out.is_file()                 # and its files survive


def test_never_retention_deletes_nothing(tmp_path):
    data, db, store = _setup(tmp_path)
    now = datetime.now(timezone.utc)
    _media_row(db, "m1", SPEECH)
    _job(store, "m1", state="completed",
         finished_at=now - timedelta(days=365))
    stats = sweep(store, data, None, now=now)
    assert stats["jobs"] == 0
    assert store.list() != []


def test_retention_race_rechecks_state(tmp_path):
    """A job listed as expired but re-queued (retry) before the delete
    reaches it must survive — the sweep re-reads the row."""
    data, db, store = _setup(tmp_path)
    now = datetime.now(timezone.utc)
    _media_row(db, "m1", SPEECH)
    j = _job(store, "m1", state="completed",
             finished_at=now - timedelta(days=30))
    jdir = job_dir(j.id, data)

    # Between listing and deleting, the row flips back to queued
    # (a retry created work the sweep must not kill). Simulate by
    # flipping the row just before sweep's per-job re-check.
    real_expired = store.expired_terminal_jobs
    fired = {"n": 0}
    def _flip(cutoff_iso):
        jobs = real_expired(cutoff_iso)
        if jobs and not fired["n"]:
            fired["n"] = 1
            con = connect(db)
            try:
                con.execute(
                    "UPDATE jobs SET state='queued', finished_at=NULL"
                    " WHERE id=?", (jobs[0].id,),
                )
                con.commit()
            finally:
                con.close()
        return jobs
    store.expired_terminal_jobs = _flip  # type: ignore[method-assign]

    stats = sweep(store, data, 7, now=now)
    assert stats["jobs"] == 0
    assert store.get(j.id).state == "queued"
    assert jdir.exists()


def test_junction_escape_never_deletes_target(tmp_path):
    """A junction inside a job dir pointing outside data/ must be
    unlinked, never followed — its target and contents stay."""
    data, db, store = _setup(tmp_path)
    now = datetime.now(timezone.utc)
    _media_row(db, "m1", SPEECH)
    j = _job(store, "m1", state="completed",
             finished_at=now - timedelta(days=30))
    jdir = job_dir(j.id, data)

    outside = tmp_path / "outside"
    outside.mkdir()
    canary = outside / "keep-me.txt"
    canary.write_text("precious", encoding="utf-8")

    link = jdir / "work" / "evil_link"
    if sys.platform == "win32":
        r = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            pytest.skip(f"mklink /J unavailable: {r.stdout}{r.stderr}")
    else:
        os.symlink(outside, link, target_is_directory=True)
    assert link.is_dir()  # through the junction the target is visible

    stats = sweep(store, data, 7, now=now)
    assert stats["jobs"] == 1
    assert not jdir.exists()
    assert canary.read_text(encoding="utf-8") == "precious"


def test_junction_as_job_dir_root(tmp_path):
    """data/jobs/<id> itself a junction → only the link is removed."""
    data, db, store = _setup(tmp_path)
    now = datetime.now(timezone.utc)
    _media_row(db, "m1", SPEECH)
    j = _job(store, "m1", state="completed",
             finished_at=now - timedelta(days=30))
    jdir = data / "jobs" / j.id
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("x", encoding="utf-8")

    jdir.parent.mkdir(parents=True, exist_ok=True)
    if jdir.exists():
        import shutil
        shutil.rmtree(jdir)
    if sys.platform == "win32":
        r = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(jdir), str(outside)],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            pytest.skip(f"mklink /J unavailable: {r.stdout}{r.stderr}")
    else:
        os.symlink(outside, jdir, target_is_directory=True)

    stats = sweep(store, data, 7, now=now)
    assert stats["jobs"] == 1
    assert not jdir.exists()
    assert (outside / "keep.txt").read_text(encoding="utf-8") == "x"


def test_rmtree_safe_refuses_outside_root(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    assert _rmtree_safe(outside, root) == 0
    assert outside.exists()


def test_active_preparation_media_skipped(tmp_path):
    """Media being prepared (skip_media set) is never deleted even with
    no referencing job."""
    data, db, store = _setup(tmp_path)
    now = datetime.now(timezone.utc)
    _media_row(db, "m1", SPEECH)
    media_dir("m1", data).mkdir(parents=True)
    stats = sweep(store, data, 7, now=now, skip_media={"m1"})
    assert stats["media"] == 0
    assert media_dir("m1", data).exists()


# ---- API surface ---------------------------------------------------------------


def _client(tmp_path):
    return make_client(
        create_app(
            Settings(data_dir=tmp_path / "d", db_path=tmp_path / "d" / "app.db")
        )
    )


def test_retention_setting_api(tmp_path):
    c = _client(tmp_path)
    r = c.get("/api/settings")
    assert r.status_code == 200 and r.json()["retention_days"] == 7
    r = c.put("/api/settings/retention", json={"days": 30})
    assert r.status_code == 200 and r.json()["retention_days"] == 30
    r = c.get("/api/settings")
    assert r.json()["retention_days"] == 30
    r = c.put("/api/settings/retention", json={"days": "never"})
    assert r.json()["retention_days"] == "never"
    r = c.get("/api/settings")
    assert r.json()["retention_days"] == "never"
    for bad in (0, 2, 365, "x"):
        r = c.put("/api/settings/retention", json={"days": bad})
        assert r.status_code == 422, bad


def test_keep_endpoint(tmp_path):
    c = _client(tmp_path)
    r = c.post(
        "/api/media",
        content=SPEECH.read_bytes(),
        headers={
            "X-Filename": quote(SPEECH.name),
            "Content-Type": "application/octet-stream",
        },
    )
    mid = r.json()["media_id"]
    jid = c.post(
        "/api/jobs", json={"media_id": mid, "device": "cpu"}
    ).json()["id"]
    r = c.post(f"/api/jobs/{jid}/keep", json={"keep": True})
    assert r.status_code == 200 and r.json()["keep"] is True
    r = c.post(f"/api/jobs/{jid}/keep", json={"keep": False})
    assert r.json()["keep"] is False
    assert c.post(
        f"/api/jobs/{'0' * 32}/keep", json={"keep": True}
    ).status_code == 404


def test_fresh_unprocessed_upload_survives(tmp_path):
    """Orchestrator review: an upload with no job yet (the user is still
    choosing steps) must never be swept — only unreferenced media older
    than the retention cutoff go."""
    data, db, store = _setup(tmp_path)
    now = datetime.now(timezone.utc)
    _media_row(db, "fresh", SPEECH, created_at=now.isoformat())
    media_dir("fresh", data).mkdir(parents=True)
    _media_row(db, "stale", SPEECH,
               created_at=(now - timedelta(days=30)).isoformat())
    media_dir("stale", data).mkdir(parents=True)

    stats = sweep(store, data, 7, now=now)

    assert stats["media"] == 1
    assert media_dir("fresh", data).exists()
    assert not media_dir("stale", data).exists()
