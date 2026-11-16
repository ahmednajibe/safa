"""M10 task 12 — performance check.

Times a full end-to-end job (Podcast preset ops) on a synthetic-speech
file of a chosen length, on a chosen device, against a real uvicorn on
a scratch data dir (like tests/e2e_server.py). While the job runs it
samples `GET /` + `GET /api/health` latency — the "UI stays responsive"
half of the requirement.

    uv run python scripts/perf_m10.py --device gpu --minutes 60
    uv run python scripts/perf_m10.py --device cpu --minutes 10

Prints one JSON object to stdout; pass --out to also save it.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402
import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402

BASE_SRC = ROOT / "tests" / "fixtures" / "generated" / "speech.wav"
# data/ is gitignored; .pytest_tmp is wiped by pytest basetemp each session.
SCRATCH = ROOT / "data" / "tmp" / "perf"
PORT = 8791
PODCAST_OPS = {
    "highpass": {},
    "dehum": {},
    "declick": {"mode": "mouth"},
    "denoise": {"engine": "ai", "strength_db": 24},
    "dereverb": {"strength": "gentle"},
    "breaths": {},
    "enhance": {},
    "silence": {"action": "remove"},
    "loudness": {"target": "podcast"},
}


def make_speech(minutes: int, dest: Path) -> Path:
    """Tile the SAPI fixture with 0.5 s gaps — streaming write, ~345 MB/h."""
    out = dest / f"speech_{minutes}min.wav"
    if out.exists():
        return out
    src, sr = sf.read(BASE_SRC, dtype="float32", always_2d=True)
    gap = np.zeros((int(sr * 0.5), src.shape[1]), dtype="float32")
    block = np.concatenate([src, gap])
    need = int(minutes * 60 * sr)
    print(f"building {out.name} ({minutes} min, {sr} Hz)…", flush=True)
    with sf.SoundFile(
        out, "w", samplerate=sr, channels=src.shape[1], subtype="PCM_16"
    ) as f:
        written = 0
        while written < need:
            chunk = block[: min(len(block), need - written)]
            f.write(chunk)
            written += len(chunk)
    return out


def wait_health(base: str, server: subprocess.Popen, timeout=90.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if server.poll() is not None:
            raise RuntimeError(f"uvicorn exited early ({server.returncode})")
        try:
            if httpx.get(f"{base}/api/health", timeout=3).status_code == 200:
                return
        except httpx.TransportError:
            pass
        time.sleep(1)
    raise TimeoutError("uvicorn never became healthy")


def run(device: str, minutes: int, out_path: Path | None) -> int:
    SCRATCH.mkdir(parents=True, exist_ok=True)
    wav = make_speech(minutes, SCRATCH)
    tmp = Path(tempfile.mkdtemp(prefix="ss_perf_", dir=SCRATCH))
    env = dict(
        os.environ,
        SS_E2E_DATA=str(tmp / "data"),
        SOUND_SYSTEM_PORT=str(PORT),  # Host allow-list tracks settings.port
    )
    base = f"http://127.0.0.1:{PORT}"
    log = open(SCRATCH / f"uvicorn_{device}_{minutes}m.log", "wb")
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "tests.e2e_server:app",
         "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=str(ROOT), env=env,
        stdout=log, stderr=log,
    )
    try:
        wait_health(base, server)
        c = httpx.Client(base_url=base, timeout=httpx.Timeout(600.0))
        r = c.post(
            "/api/media",
            content=wav.read_bytes(),
            headers={"X-Filename": wav.name,
                     "Content-Type": "application/octet-stream"},
        )
        r.raise_for_status()
        media_id = r.json()["media_id"]
        r = c.post("/api/jobs", json={
            "media_id": media_id, "device": device,
            "ops": PODCAST_OPS,
            "output": {"audio_format": "wav", "keep_video": False},
        })
        if r.status_code != 201:
            print(f"job rejected: {r.status_code} {r.text}", file=sys.stderr)
            return 2
        jid = r.json()["id"]
        print(f"job {jid} started — {minutes} min on {device}", flush=True)

        lat_index, lat_health, state_log = [], [], []
        t0 = time.monotonic()
        state = "queued"
        while state not in ("completed", "failed", "cancelled", "interrupted"):
            try:
                t = time.monotonic()
                c.get("/")
                lat_index.append(time.monotonic() - t)
                t = time.monotonic()
                c.get("/api/health")
                lat_health.append(time.monotonic() - t)
            except Exception:
                pass
            try:
                job = c.get(f"/api/jobs/{jid}").json()
                state = job["state"]
            except Exception as exc:
                print(f"  poll error (retrying): {exc}", flush=True)
                time.sleep(5)
                continue
            op = job.get("stage_id") or job.get("stage") or "?"
            pct = job.get("stage_pct", "?")
            state_log.append({"t": round(time.monotonic() - t0, 1),
                              "state": state, "op": op, "pct": pct})
            print(f"  {state_log[-1]}", flush=True)
            time.sleep(5)
        secs = time.monotonic() - t0
        try:
            job = c.get(f"/api/jobs/{jid}").json()
        except Exception:
            pass
        if state != "completed":
            print(f"job ended in state={state}: {job}", file=sys.stderr)
        result = {
            "device": device,
            "minutes": minutes,
            "file": wav.name,
            "state": state,
            "wall_seconds": round(secs, 1),
            "realtime_ratio": round(secs / (minutes * 60), 3),
            "index_latency_s": {
                "n": len(lat_index), "median": round(statistics.median(lat_index), 4),
                "max": round(max(lat_index), 4),
            },
            "health_latency_s": {
                "n": len(lat_health), "median": round(statistics.median(lat_health), 4),
                "max": round(max(lat_health), 4),
            },
            "error": job.get("error"),
            "steps": job.get("steps"),
            "state_log_tail": state_log[-10:],
        }
        print(json.dumps(result, indent=2, default=str))
        if out_path:
            out_path.write_text(json.dumps(result, indent=2, default=str))
        return 0 if state == "completed" else 1
    finally:
        if server.poll() is None:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(server.pid)],
                           capture_output=True)
        try:
            server.wait(timeout=15)
        except Exception:
            pass
        log.close()
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", choices=["gpu", "cpu"], required=True)
    ap.add_argument("--minutes", type=int, default=60)
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args()
    return run(a.device, a.minutes, a.out)


if __name__ == "__main__":
    raise SystemExit(main())
