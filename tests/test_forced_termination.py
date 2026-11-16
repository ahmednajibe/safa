"""M10 T9: forced termination leaves no orphan worker/ffmpeg; the next
start reaps them and the killed job comes back ``interrupted`` + retryable.

A real uvicorn API subprocess (``tests/e2e_server.py``, SS_E2E_DATA data
dir) is hard-killed while its worker subprocess runs — the console-close
/ taskkill scenario. A second API on the same data dir must, inside its
lifespan startup, kill the orphaned worker tree and mark the job
interrupted.
"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import psutil
import pytest

from backend.db import connect

ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.slow


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _spawn_api(port: int, data: Path) -> subprocess.Popen:
    env = dict(os.environ)
    env["SS_E2E_DATA"] = str(data)
    env["SOUND_SYSTEM_PORT"] = str(port)
    env["PYTHONUTF8"] = "1"
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "tests.e2e_server:app",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(ROOT), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _get(port: int, path: str):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def _post(port: int, path: str, payload: bytes, ctype: str,
          extra: dict | None = None):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=payload, method="POST",
        headers={"Content-Type": ctype, **(extra or {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def _wait_health(proc: subprocess.Popen, port: int, timeout: float = 90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"api exited early rc={proc.returncode}")
        try:
            if _get(port, "/api/health").get("status") == "ok":
                return
        except Exception:
            pass
        time.sleep(0.4)
    raise TimeoutError("api never became healthy")


def _wait_job_state(port: int, job_id: str, want: set[str],
                    timeout: float = 60) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        j = _get(port, f"/api/jobs/{job_id}")
        if j.get("state") in want:
            return j
        time.sleep(0.3)
    raise TimeoutError(f"job {job_id} never reached {want}")


def _worker_pid(db: Path, job_id: str) -> int | None:
    con = connect(db)
    try:
        row = con.execute(
            "SELECT worker_pid FROM jobs WHERE id=?", (job_id,)
        ).fetchone()
        return int(row["worker_pid"]) if row and row["worker_pid"] else None
    finally:
        con.close()


def _tree(p: psutil.Process) -> list[psutil.Process]:
    try:
        return [p] + p.children(recursive=True)
    except psutil.NoSuchProcess:
        return []


def _kill_job_workers(job_ids: set[str]) -> None:
    """Cleanup sweep: kill only processes we spawned (cmdline carries
    'backend.worker' plus one of *our* job ids)."""
    for p in psutil.process_iter(["cmdline"]):
        try:
            cmd = [str(a) for a in (p.cmdline() or [])]
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if "backend.worker" in cmd and any(j in cmd for j in job_ids):
            try:
                p.kill()
            except psutil.NoSuchProcess:
                pass


def test_forced_termination_no_orphans(tmp_path):
    import numpy as np
    import soundfile as sf

    data = tmp_path / "data"
    db = data / "app.db"
    port1, port2 = _free_port(), _free_port()
    assert port1 != port2 != 8765

    src = tmp_path / "tone.wav"
    t = np.arange(90 * 48000) / 48000
    sf.write(
        str(src), (0.3 * np.sin(2 * np.pi * 220 * t)).astype("float32"),
        48000, subtype="FLOAT",
    )

    api1 = _spawn_api(port1, data)
    api2: subprocess.Popen | None = None
    job_id = ""
    retry_id = ""
    try:
        _wait_health(api1, port1)
        code, media = _post(
            port1, "/api/media", src.read_bytes(),
            "application/octet-stream",
            {"X-Filename": "tone.wav"},
        )
        assert code == 201, media
        code, job = _post(
            port1, "/api/jobs",
            json.dumps({
                "media_id": media["media_id"],
                "ops": {"loudness": {}},
                "output": {"audio_format": "wav", "keep_video": False},
                "device": "cpu",
            }).encode(),
            "application/json",
        )
        assert code == 201, job
        job_id = job["id"]

        _wait_job_state(port1, job_id, {"running"}, timeout=60)
        wpid = _worker_pid(db, job_id)
        assert wpid and psutil.pid_exists(wpid), "no live worker pid"
        worker = psutil.Process(wpid)
        kids = worker.children(recursive=True)
        # Freeze the tree so it cannot finish before the second boot —
        # the orphan must still be 'running' when api2 starts.
        for p in [worker] + kids:
            try:
                p.suspend()
            except psutil.NoSuchProcess:
                pass

        api1.kill()  # hard kill — console-close / taskkill equivalent
        api1.wait(timeout=10)
        assert psutil.pid_exists(wpid), "worker died with its parent"

        api2 = _spawn_api(port2, data)
        _wait_health(api2, port2)  # lifespan reaper ran by now

        # Orphan worker + every descendant must be gone.
        assert not psutil.pid_exists(wpid), "orphan worker survived restart"
        for k in kids:
            assert not psutil.pid_exists(k.pid), (
                f"worker descendant {k.pid} survived restart"
            )

        j = _get(port2, f"/api/jobs/{job_id}")
        assert j["state"] == "interrupted", j["state"]

        code, retry = _post(port2, f"/api/jobs/{job_id}/retry", b"{}",
                            "application/json")
        assert code == 201, retry
        retry_id = retry["id"]
        assert retry["state"] == "queued"
        _post(port2, f"/api/jobs/{retry_id}/cancel", b"{}",
              "application/json")
    finally:
        for proc in (api1, api2):
            if proc is not None and proc.poll() is None:
                proc.kill()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
        _kill_job_workers({job_id, retry_id} - {""})
