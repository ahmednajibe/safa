"""M3b Task 3 tests: supervisor — one job at a time, env, cancel, shutdown.

These spawn real ``python -m backend.worker`` subprocesses (fast:
pass-through jobs, no ML ops yet) so process-tree behaviour is covered for
real, not mocked.
"""
import json
import time
from pathlib import Path

import psutil
import pytest

from backend.core.ffmpeg import find_ffmpeg
from backend.core.proc import run
from backend.core.settings import Settings
from backend.db import connect, init_db
from backend.jobs.store import JobStore
from backend.jobs.supervisor import Supervisor
from backend.media.probe import probe

GENERATED = Path(__file__).parent / "fixtures" / "generated"
ARABIC_WAV = GENERATED / "تسجيل اختبار 1.wav"


def _setup(tmp_path):
    db = tmp_path / "app.db"
    data = tmp_path / "data"
    (data / "jobs").mkdir(parents=True, exist_ok=True)
    init_db(db)
    return db, data, JobStore(db), Settings(data_dir=data, db_path=db)


def _media(db, media_id, path: Path):
    con = connect(db)
    try:
        con.execute(
            "INSERT INTO media (id, filename, stored_path, size_bytes,"
            " probe_json, created_at) VALUES (?,?,?,?,?,?)",
            (
                media_id, path.name, str(path), path.stat().st_size,
                json.dumps(probe(path).to_dict()), "2026-11-15T00:00:00",
            ),
        )
        con.commit()
    finally:
        con.close()


def _job(store, media_id="m", fmt="mp3", **kw):
    return store.create(
        media_id=media_id, kind="full", ops=[],
        output={"audio_format": fmt, "keep_video": False},
        preview=None, device=kw.pop("device", "cpu"),
        audio_stream=kw.pop("audio_stream", 0), **kw,
    )


def _wait_state(store, job_id, states: set, timeout: float) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = store.get(job_id).state
        if state in states:
            return state
        time.sleep(0.05)
    raise AssertionError(
        f"job {job_id} never reached {states}; last={store.get(job_id).state}"
    )


def _cmds_with(needle: str) -> list[list]:
    """Cmdlines of live processes mentioning ``needle``."""
    out = []
    for p in psutil.process_iter(["cmdline"]):
        try:
            cmd = p.cmdline() or []
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if any(needle in str(a) for a in cmd):
            out.append(cmd)
    return out


def _long_wav(tmp_path) -> Path:
    """600 s stereo wav: mp3 encode takes long enough to cancel mid-flight."""
    p = tmp_path / "long.wav"
    run(
        [
            find_ffmpeg(), "-hide_banner", "-y",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=600",
            "-ar", "48000", "-ac", "2", "-acodec", "pcm_s16le", p,
        ],
        timeout=120,
    )
    return p


# ---- worker env ----------------------------------------------------------------


def test_worker_env_cpu_hides_cuda(tmp_path):
    db, data, store, settings = _setup(tmp_path)
    _media(db, "m", ARABIC_WAV)
    cpu_job = _job(store, device="cpu")
    gpu_job = _job(store, device="gpu")
    sup = Supervisor(store, settings)

    env = sup.worker_env(cpu_job)
    assert env["CUDA_VISIBLE_DEVICES"] == ""
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert env["SOUND_SYSTEM_DB"] == str(db)
    assert env["SOUND_SYSTEM_DATA"] == str(data)

    gpu_env = sup.worker_env(gpu_job)
    # gpu jobs keep whatever the parent has — never force-hidden
    assert gpu_env.get("CUDA_VISIBLE_DEVICES", "x") != ""


# ---- one at a time (Review Focus 3) ---------------------------------------------


def test_one_at_a_time(tmp_path, real_dir):
    db, data, store, settings = _setup(tmp_path)
    _media(db, "m", real_dir / "Recording (3).m4a")
    sup = Supervisor(store, settings)
    sup.start()
    try:
        j1 = _job(store, fmt="mp3")
        j2 = _job(store, fmt="wav")
        sup.wake()

        state = _wait_state(store, j1.id, {"running", "completed"}, 30)
        if state == "running":
            # while the first runs the second must still be queued
            assert store.get(j2.id).state == "queued"
            assert store.queue_position(j2.id) == 1

        assert _wait_state(store, j1.id, {"completed"}, 90) == "completed"
        assert _wait_state(store, j2.id, {"completed"}, 90) == "completed"
        f1, f2 = store.get(j1.id), store.get(j2.id)
        # serialisation invariant: job 2 never started before job 1 finished
        assert f1.finished_at <= f2.started_at
    finally:
        sup.stop()


# ---- cancel kills the whole tree (Review Focus 1) --------------------------------


def test_cancel_kills_tree(tmp_path):
    db, data, store, settings = _setup(tmp_path)
    _media(db, "m", _long_wav(tmp_path))
    sup = Supervisor(store, settings)
    sup.start()
    try:
        j = _job(store, fmt="mp3")
        sup.wake()

        # wait until the worker has actually spawned its ffmpeg child
        deadline = time.time() + 30
        ffmpeg_seen = False
        while time.time() < deadline:
            cmds = _cmds_with(j.id)
            if any(
                c and "ffmpeg" in Path(str(c[0])).name.lower() for c in cmds
            ):
                ffmpeg_seen = True
                break
            time.sleep(0.1)
        assert ffmpeg_seen, "worker never spawned an ffmpeg child"

        sup.cancel(j.id)

        deadline = time.time() + 10
        while time.time() < deadline and _cmds_with(j.id):
            time.sleep(0.1)
        assert not _cmds_with(j.id), (
            f"processes still alive: {_cmds_with(j.id)}"
        )
        assert _wait_state(store, j.id, {"cancelled"}, 15) == "cancelled"
        # partial outputs removed (Review Focus 1)
        out = data / "jobs" / j.id / "output"
        assert not out.exists() or not any(out.iterdir())
    finally:
        sup.stop()


# ---- shutdown marks running job interrupted --------------------------------------


def test_shutdown_marks_interrupted(tmp_path):
    db, data, store, settings = _setup(tmp_path)
    _media(db, "m", _long_wav(tmp_path))
    sup = Supervisor(store, settings)
    sup.start()
    j = _job(store, fmt="mp3")
    sup.wake()
    _wait_state(store, j.id, {"running"}, 30)

    sup.stop(timeout=15)

    got = store.get(j.id)
    assert got.state == "interrupted"
    assert "app was closed" in got.message
    assert not _cmds_with(j.id)
