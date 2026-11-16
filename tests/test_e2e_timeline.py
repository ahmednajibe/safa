"""M4b Task 4: timeline+video end-to-end through /api/jobs.

clip.mp4 with trim(1,8) + speed 1.25 + loudness must complete with the
output mp4 at the timeline's out_duration; /api/jobs/{id}/timeline serves
the map. A trimmed audio-only real recording also leaves timeline.json.
Review artifacts belong in ``tmp_path`` (M5 rule: pytest never writes to
``data/review`` — ``scripts/make_review_files.py`` owns that directory).
"""
import json
import threading
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from backend.core.settings import Settings
from backend.main import create_app
from tests.client import make_client
from backend.media.probe import probe

GENERATED = Path(__file__).parent / "fixtures" / "generated"
CLIP = GENERATED / "clip.mp4"


def _client(tmp_path) -> TestClient:
    return make_client(
        create_app(Settings(data_dir=tmp_path, db_path=tmp_path / "app.db"))
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


def _collect_sse(c, jid: str, timeout: float = 240):
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


def test_trim_speed_loudness_video_job(tmp_path):
    """clip.mp4 + trim(1,8) + speed 1.25 + loudness: completes, output mp4
    duration = timeline out_duration ±1 frame, /timeline shows the cuts."""
    c = _client(tmp_path)
    with c:
        mid = _upload(c, CLIP)
        r = c.post(
            "/api/jobs",
            json={
                "media_id": mid,
                "device": "cpu",
                "ops": {
                    "trim": {"start_s": 1.0, "end_s": 8.0},
                    "speed": {"factor": 1.25},
                    "loudness": {"target": "podcast"},
                },
                "output": {"audio_format": "wav", "keep_video": True},
            },
        )
        assert r.status_code == 201, r.text
        jid = r.json()["id"]

        events = _collect_sse(c, jid)
        final = events[-1][1]
        assert final["state"] == "completed", events[-1]

        r = c.get(f"/api/jobs/{jid}/timeline")
        assert r.status_code == 200, r.text
        tl = r.json()
        assert tl["removed"][0] == [0.0, 1.0]
        assert tl["removed"][1][0] == 8.0
        out_dur = (
            sum(s["out_end"] - s["out_start"] for s in tl["segments"])
        )
        assert out_dur == pytest.approx(7.0 / 1.25, abs=0.01)

        info = c.get(f"/api/jobs/{jid}").json()
        video = [f for f in info["files"] if f["kind"] == "video"]
        assert len(video) == 1 and video[0]["name"].endswith(".mp4")
        stored = Path(
            next(f for f in c.app.state.store.files(jid)
                 if f["kind"] == "video")["path"]
        )
        p = probe(stored)
        frame = 1.0 / 25.0
        assert p.video[0].duration_s == pytest.approx(out_dur, abs=frame)
        assert p.audio[0].duration_s == pytest.approx(out_dur, abs=frame)
        assert p.video[0].codec == "h264"


def test_timeline_404_until_completed(tmp_path):
    c = _client(tmp_path)
    with c:
        mid = _upload(c, CLIP)
        r = c.post("/api/jobs", json={"media_id": mid, "device": "cpu"})
        assert r.status_code == 201
        jid = r.json()["id"]
        # queued or running — timeline.json not written yet
        early = c.get(f"/api/jobs/{jid}/timeline")
        assert early.status_code == 404
        events = _collect_sse(c, jid)
        assert events[-1][1]["state"] == "completed"
        assert c.get(f"/api/jobs/{jid}/timeline").status_code == 200


def test_trim_audio_only_real_recording(tmp_path, real_dir):
    """Recording.m4a (audio only) with trim → completed + timeline.json."""
    c = _client(tmp_path)
    with c:
        mid = _upload(c, real_dir / "Recording.m4a")
        r = c.post(
            "/api/jobs",
            json={
                "media_id": mid,
                "device": "cpu",
                "ops": {"trim": {"start_s": 2.0, "end_s": 12.0}},
                "output": {"audio_format": "wav", "keep_video": False},
            },
        )
        assert r.status_code == 201, r.text
        jid = r.json()["id"]
        events = _collect_sse(c, jid)
        assert events[-1][1]["state"] == "completed"

        r = c.get(f"/api/jobs/{jid}/timeline")
        assert r.status_code == 200, r.text
        tl = r.json()
        assert tl["removed"][0] == [0.0, 2.0]
        assert tl["removed"][1][0] == 12.0
        assert len(tl["segments"]) == 1
        seg = tl["segments"][0]
        assert seg["src_start"] == pytest.approx(2.0)
        assert seg["src_end"] == pytest.approx(12.0)
