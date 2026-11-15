"""M5 Task 5: "Cut silence" end-to-end through /api/jobs.

- A jump-cut mp4 (testsrc + ``speech_silences.wav``) processed with
  ``{"silence": {}}`` (defaults = remove): audio and video durations
  agree within one frame and equal ``timeline.out_duration``; the
  ``/timeline`` endpoint lists the removed spans (Review Focus 4).
- A real recording through the "Podcast cleanup" ops minus the
  not-yet-implemented ones: completes, shorter than input, loudness on
  target.
- Slow: a 30-minute generated file completes in a worker whose peak RSS
  stays < 1.5 GB (psutil-measured).
"""
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import quote

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from backend.core.ffmpeg import find_ffmpeg
from backend.core.settings import Settings
from backend.main import create_app
from backend.media.loudness import integrated_lufs
from backend.media.probe import probe
from backend.media.timeline import Timeline

GENERATED = Path(__file__).parent / "fixtures" / "generated"
SPEECH_SIL = GENERATED / "speech_silences.wav"
SPEECH = GENERATED / "speech.wav"


def _client(tmp_path) -> TestClient:
    return TestClient(
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


def _collect_sse(c, jid: str, timeout: float = 300):
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


def _job_output(c, jid: str, kind: str) -> Path:
    return Path(
        next(
            f for f in c.app.state.store.files(jid) if f["kind"] == kind
        )["path"]
    )


def _make_jumpcut_src(tmp_path: Path) -> Path:
    """testsrc video + speech_silences.wav audio → ~23.7 s mp4.

    The video runs 1 s longer than the audio so the tail kept span can
    always find frames (a video shorter than its audio would cost the
    output a frame or two at the very end).
    """
    out = tmp_path / "jump_src.mp4"
    dur = sf.info(str(SPEECH_SIL)).frames / 48000
    subprocess.run(
        [
            str(find_ffmpeg()), "-hide_banner", "-y",
            "-f", "lavfi",
            "-i", f"testsrc=size=640x360:rate=25:duration={dur + 1.0:.3f}",
            "-i", str(SPEECH_SIL),
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", str(out),
        ],
        check=True, capture_output=True, timeout=120,
    )
    return out


# ---- Review Focus 4: video jump cut through the API ------------------------------


def test_silence_defaults_remove_through_api(tmp_path):
    """``{"silence": {}}`` → action=remove by default: the jump-cut mp4
    comes out shorter, A/V durations agree within one frame, both equal
    ``timeline.out_duration``, and /timeline lists removed spans."""
    c = _client(tmp_path)
    src = _make_jumpcut_src(tmp_path)
    in_dur = probe(src).duration_s
    with c:
        mid = _upload(c, src)
        r = c.post(
            "/api/jobs",
            json={
                "media_id": mid,
                "device": "cpu",
                "ops": {"silence": {}},
                "output": {"audio_format": "wav", "keep_video": True},
            },
        )
        assert r.status_code == 201, r.text
        assert r.json()["disabled"] == {}
        jid = r.json()["id"]
        events = _collect_sse(c, jid)
        final = events[-1][1]
        assert final["state"] == "completed", events[-1]
        assert final["message_code"] == "job_done"

        tl = Timeline.from_dict(c.get(f"/api/jobs/{jid}/timeline").json())
        assert len(tl.removed_spans()) >= 2  # the two fixture silences
        assert not tl.is_identity

        audio = _job_output(c, jid, "audio")
        video = _job_output(c, jid, "video")
        pa, pv = probe(audio), probe(video)
        # ffprobe reports the h264 stream duration as last-PTS, which can
        # sit a frame below the track end; allow a frame + slack.
        frame = 1.0 / 25.0
        assert pv.video[0].duration_s == pytest.approx(
            tl.out_duration, abs=frame + 0.001
        )
        assert pv.audio[0].duration_s == pytest.approx(
            tl.out_duration, abs=frame
        )
        assert pa.duration_s == pytest.approx(tl.out_duration, abs=frame)
        # silence really left: output well below input
        assert pv.duration_s < in_dur - 7.0

        # R1 B8 + weak-test: the op's coded summary must be visible in
        # the SSE stream itself. Stage records are cumulative
        # (stages_json rides every state event), so after the silence
        # stage closes, "silence_removed" is deterministic — not a racy
        # transient message_code on the job row.
        assert any(
            s.get("message_code") == "silence_removed"
            for ev, p in events
            if ev in ("state", "done")
            for s in p.get("stages") or []
        ), "silence_removed never reached an SSE payload"
        fin = next(
            s for s in final["stages"]
            if s.get("message_code") == "silence_removed"
        )
        assert fin["message_params"]["seconds"] > 5.0
        assert fin["state"] == "done"


def test_silence_message_code_through_runner(tmp_path):
    """The op's coded summary reaches ``on_progress`` (Task 0 contract at
    runner level — the SSE job-row replay can legitimately miss it)."""
    from backend.pipeline.runner import run_pipeline
    from backend.pipeline.types import OutputSpec

    p = probe(SPEECH_SIL)
    events = []
    run_pipeline(
        media_path=SPEECH_SIL,
        probe=p,
        audio_stream_index=p.audio[0].index,
        ops=[("silence", {})],
        output=OutputSpec(),
        job_root=tmp_path / "job",
        on_progress=events.append,
        should_cancel=lambda: False,
        device="cpu",
    )
    msgs = [e for e in events if e.message_code == "silence_removed"]
    assert msgs, "no silence_removed progress row"
    params = msgs[-1].message_params
    assert params["seconds"] > 5.0  # fixture holds ~9.6 s of silence
    assert params["count"] >= 2


# ---- podcast preset on a real recording --------------------------------------------


def test_podcast_preset_e2e_real_recording(tmp_path, real_dir):
    """"Podcast cleanup" minus the unimplemented ops: completes, output
    shorter than input (silence cut), loudness on the −16 LUFS target."""
    c = _client(tmp_path)
    src = real_dir / "Recording (2).m4a"
    in_dur = probe(src).duration_s
    with c:
        mid = _upload(c, src)
        r = c.post(
            "/api/jobs",
            json={
                "media_id": mid,
                "device": "cpu",
                "ops": {
                    "highpass": {},
                    "dehum": {},
                    "declick": {"mode": "mouth"},
                    "enhance": {},
                    "silence": {"action": "remove"},
                    "loudness": {"target": "podcast"},
                },
                "output": {"audio_format": "wav", "keep_video": False},
            },
        )
        assert r.status_code == 201, r.text
        assert r.json()["disabled"] == {}
        jid = r.json()["id"]
        events = _collect_sse(c, jid)
        assert events[-1][1]["state"] == "completed"

        audio = _job_output(c, jid, "audio")
        p = probe(audio)
        assert p.duration_s < in_dur - 0.5  # silence was removed
        lufs = integrated_lufs(audio)
        assert lufs is not None and abs(lufs - (-16.0)) <= 0.5


# ---- Review Focus 5 (slow): 30-minute file, bounded RSS ---------------------------


def _make_long_speech(path: Path, target_s: float = 1800.0) -> None:
    """speech_silences.wav repeated with a 1 s gap until ≥ target_s."""
    src, sr = sf.read(str(SPEECH_SIL), dtype="float32", always_2d=True)
    gap = np.zeros((sr, 1), dtype=np.float32)
    written = 0
    with sf.SoundFile(
        str(path), "w", samplerate=sr, channels=1,
        format="WAV", subtype="FLOAT",
    ) as f:
        while written < int(target_s * sr):
            f.write(src)
            f.write(gap)
            written += len(src) + sr


@pytest.mark.slow
def test_long_file_peak_rss(tmp_path):
    """30 min of repeated speech+silence through a real ``backend.worker``
    subprocess: completes and stays under 1.5 GB peak RSS."""
    import psutil

    from backend.core.paths import ROOT
    from backend.db import init_db
    from backend.jobs.store import JobStore

    db = tmp_path / "app.db"
    init_db(db)
    long_wav = tmp_path / "long.wav"
    _make_long_speech(long_wav, target_s=1800.0)

    store = JobStore(db)
    con = store._con()
    try:
        con.execute(
            "INSERT INTO media (id, filename, stored_path, size_bytes,"
            " probe_json, created_at) VALUES (?,?,?,?,?,?)",
            (
                "m1", "long.wav", str(long_wav), long_wav.stat().st_size,
                json.dumps(probe(long_wav).to_dict()),
                "2026-11-15T00:00:00",
            ),
        )
        con.commit()
    finally:
        con.close()
    job = store.create(
        "m1", "full", [("silence", {"action": "remove"})],
        {"audio_format": "wav", "keep_video": False},
        None, "cpu", 0,
    )

    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["SOUND_SYSTEM_DB"] = str(db)
    env["SOUND_SYSTEM_DATA"] = str(tmp_path)
    env["CUDA_VISIBLE_DEVICES"] = ""
    proc = subprocess.Popen(
        [sys.executable, "-m", "backend.worker", job.id],
        cwd=str(ROOT), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    peak = 0
    try:
        p = psutil.Process(proc.pid)
        while proc.poll() is None:
            try:
                rss = p.memory_info().rss + sum(
                    c.memory_info().rss
                    for c in p.children(recursive=True)
                )
                peak = max(peak, rss)
            except psutil.NoSuchProcess:
                break
            time.sleep(0.5)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)

    got = store.get(job.id)
    assert got is not None and got.state == "completed", (
        f"state={got and got.state} error={got and got.error}"
    )
    limit = 1.5 * (1 << 30)
    print(f"\nworker peak RSS: {peak / (1 << 30):.2f} GiB")
    assert peak < limit, f"peak RSS {peak / (1 << 30):.2f} GiB ≥ 1.5 GiB"
