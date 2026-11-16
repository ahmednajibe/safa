"""M4a Task 5: the DSP chain end-to-end through /api/jobs on the user's
real recording (Review Focus 5), plus the Podcast-cleanup-minus-unimplemented
preset. Review artifacts belong in ``tmp_path`` (M5 rule: pytest never
writes to ``data/review`` — ``scripts/make_review_files.py`` owns it)."""
import json
import threading
from pathlib import Path
from urllib.parse import quote

import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from backend.core.settings import Settings
from backend.main import create_app
from tests.client import make_client
from backend.media.loudness import integrated_lufs, true_peak_db
from backend.media.probe import probe

GENERATED = Path(__file__).parent / "fixtures" / "generated"
REAL = Path(__file__).parent / "fixtures" / "real"


def _client(tmp_path) -> TestClient:
    return make_client(
        create_app(Settings(data_dir=tmp_path, db_path=tmp_path / "app.db"))
    )


def _upload(client, path: Path, name: str | None = None) -> str:
    r = client.post(
        "/api/media",
        content=path.read_bytes(),
        headers={
            "X-Filename": quote(name or path.name),
            "Content-Type": "application/octet-stream",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["media_id"]


def _collect_sse(c, jid: str, timeout: float = 180):
    got: list[tuple[str, object]] = []

    def _run():
        with c.stream("GET", f"/api/jobs/{jid}/events") as r:
            ev = None
            for line in r.iter_lines():
                if line.startswith("event:"):
                    ev = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    raw = line.split(":", 1)[1].strip()
                    try:
                        payload = json.loads(raw)
                    except json.JSONDecodeError:
                        payload = raw
                    got.append((ev, payload))
                    if ev == "done":
                        break
                elif line.startswith(":"):
                    got.append(("ping", line))

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout)
    assert not t.is_alive(), "SSE stream never sent 'done'"
    return got


def test_dsp_chain_e2e_real_recording(tmp_path, real_dir):
    """channel(mono) → highpass → dehum → declick → enhance → loudness on
    Recording.m4a: completes through the real supervisor/worker, output is a
    valid mono 48 kHz wav at −16 ±0.5 LUFS / TP ≤ −0.9 dBTP."""
    src = real_dir / "Recording.m4a"
    c = _client(tmp_path)
    with c:  # lifespan: supervisor thread spawns the worker subprocess
        mid = _upload(c, src)
        r = c.post(
            "/api/jobs",
            json={
                "media_id": mid,
                "device": "cpu",
                "ops": {
                    "channel": {"mode": "mono"},
                    "highpass": {},
                    "dehum": {},
                    "declick": {},
                    "enhance": {},
                    "loudness": {"target": "podcast"},
                },
                "output": {"audio_format": "wav", "keep_video": False},
            },
        )
        assert r.status_code == 201, r.text
        jid = r.json()["id"]

        events = _collect_sse(c, jid)
        final = events[-1][1]
        assert final["state"] == "completed", events[-1]
        assert final["overall_pct"] == 1.0
        assert all(s["device"] == "cpu" for s in final["stages"])

        info = c.get(f"/api/jobs/{jid}").json()
        assert info["state"] == "completed"
        audio = [f for f in info["files"] if f["kind"] == "audio"]
        assert len(audio) == 1 and audio[0]["name"].endswith(".wav")

        stored = Path(
            c.app.state.store.files(jid)[0]["path"]
        )
        assert stored.is_file()
        # plays: ffprobe reads it; format/channel/duration are right
        p = probe(stored)
        assert p.audio[0].codec in ("pcm_f32le", "pcm_s24le", "pcm_s16le")
        assert p.audio[0].sample_rate == 48000
        assert p.audio[0].channels == 1
        assert abs(p.duration_s - probe(src).duration_s) < 0.1
        lufs = integrated_lufs(stored)
        assert lufs is not None and abs(lufs - (-16.0)) <= 0.5
        assert true_peak_db(stored) <= -0.9


def test_podcast_preset_subset_e2e(tmp_path, real_dir):
    """"Podcast cleanup" minus the ops that land in later milestones
    (denoise, silence): highpass → enhance → loudness completes and the
    response's ``disabled`` map holds nothing unexpected."""
    c = _client(tmp_path)
    with c:
        mid = _upload(c, real_dir / "Recording (2).m4a")
        r = c.post(
            "/api/jobs",
            json={
                "media_id": mid,
                "device": "cpu",
                "ops": {
                    "highpass": {},
                    "enhance": {},
                    "loudness": {"target": "podcast"},
                },
                "output": {"audio_format": "wav", "keep_video": False},
            },
        )
        assert r.status_code == 201, r.text
        body = r.json()
        unexpected = set(body["disabled"])
        assert not unexpected, f"unexpected disabled ops: {unexpected}"

        jid = body["id"]
        events = _collect_sse(c, jid)
        assert events[-1][1]["state"] == "completed"
        assert all(
            s["device"] == "cpu" for s in events[-1][1]["stages"]
        )
