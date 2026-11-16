"""M10 T3/T4: shared model downloader + download API + offline proof.

- ``fetch_file``: .part resume, atomic rename, sha-256 verify, cancel.
- ``download_model``/``_download_deepfilternet3``: allowlist, zip →
  extract → ``DeepFilterNet3`` dir (no network — fetch_file is stubbed).
- ``/api/models*``: list, unknown → 404, bundled model → done, a stub
  child emits progress + done / hangs → cancel; SSE replays snapshots.
- ``test_prefetch_core_models_incl_dfn``: the prefetch script's core set
  covers deepfilternet3 and calls the shared downloader for each model.
- ``test_offline_pipeline`` (slow): a model-heavy job (denoise AI +
  separate + silence + subtitles) completes with every outbound socket
  blocked except loopback — proving the offline guarantee end-to-end.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import socket
import subprocess
import sys
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from backend.core import model_download
from backend.core.model_download import (
    DownloadCancelled,
    DownloadError,
    UnknownModel,
    download_model,
    fetch_file,
)
from backend.core.models_manifest import MODELS
from backend.core.settings import Settings
from backend.main import create_app
from tests.client import make_client

GENERATED = Path(__file__).parent / "fixtures" / "generated"
SPEECH = GENERATED / "speech.wav"


# ---- loopback HTTP fixture ---------------------------------------------------


def _handler(content: bytes):
    class _H(BaseHTTPRequestHandler):
        def do_GET(self):
            rng = self.headers.get("Range")
            if rng and rng.startswith("bytes="):
                start = int(rng[6:].split("-")[0])
                if start >= len(content):
                    self.send_response(416)
                    self.end_headers()
                    return
                self.send_response(206)
                self.send_header("Content-Length", str(len(content) - start))
                self.end_headers()
                self.wfile.write(content[start:])
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, *a):  # keep test output clean
            pass

    return _H


@pytest.fixture()
def server():
    content = bytes((i * 31 + 7) % 256 for i in range(200_000))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _handler(content))
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/blob.bin"
    yield type("Srv", (), {"url": url, "content": content})()
    srv.shutdown()
    srv.server_close()


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# ---- fetch_file ---------------------------------------------------------------


def test_fetch_file_downloads_and_verifies(server, tmp_path):
    dest = tmp_path / "m.bin"
    evs: list[dict] = []
    fetch_file(
        server.url, dest, on_progress=evs.append,
        sha256=_sha(server.content),
    )
    assert dest.read_bytes() == server.content
    assert not (tmp_path / "m.bin.part").exists()  # atomic rename consumed it
    assert evs and evs[-1]["pct"] == 100.0


def test_fetch_file_resumes_existing_part(server, tmp_path):
    dest = tmp_path / "m.bin"
    head = server.content[:123_456]
    (tmp_path / "m.bin.part").write_bytes(head)
    fetch_file(server.url, dest, sha256=_sha(server.content))
    assert dest.read_bytes() == server.content


def test_fetch_file_sha_mismatch_drops_part(server, tmp_path):
    dest = tmp_path / "m.bin"
    with pytest.raises(DownloadError):
        fetch_file(server.url, dest, sha256="0" * 64)
    assert not dest.exists()
    assert not (tmp_path / "m.bin.part").exists()


def test_fetch_file_cancelled_before_start(server, tmp_path):
    dest = tmp_path / "m.bin"
    with pytest.raises(DownloadCancelled):
        fetch_file(server.url, dest, cancel=lambda: True)
    assert not dest.exists()


# ---- download_model ------------------------------------------------------------


def test_download_model_allowlist():
    with pytest.raises(UnknownModel):
        download_model("not_a_model")


def test_download_model_already_present(monkeypatch):
    monkeypatch.setattr(
        "backend.core.capabilities._model_present", lambda n: True
    )
    out = download_model("deepfilternet3")
    assert out["already"] is True


def test_dfn_strategy_zip_to_dir(monkeypatch, tmp_path):
    """A fake release zip extracts to ``<dir>/DeepFilterNet3/`` — the
    exact layout ``init_df``/``_model_present`` look for."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("DeepFilterNet3/config.ini", "[df]\n")
        zf.writestr("DeepFilterNet3/checkpoints/model.ckpt", "0" * 16)
    zbytes = buf.getvalue()

    mdir = tmp_path / "deepfilternet"
    mdir.mkdir(parents=True)
    monkeypatch.setattr(model_download, "model_dir", lambda n: mdir)
    monkeypatch.setattr(
        model_download,
        "fetch_file",
        lambda url, dest, **kw: (dest.write_bytes(zbytes), dest)[1],
    )
    model_download._download_deepfilternet3(None, None)
    assert (mdir / "DeepFilterNet3" / "config.ini").is_file()
    assert (mdir / "DeepFilterNet3" / "checkpoints").is_dir()
    assert not (mdir / "DeepFilterNet3.zip").exists()
    assert not (mdir / ".dfn_extract").exists()


def test_dfn_strategy_skips_when_present(monkeypatch, tmp_path):
    mdir = tmp_path / "deepfilternet"
    (mdir / "DeepFilterNet3").mkdir(parents=True)
    (mdir / "DeepFilterNet3" / "config.ini").write_text("x")
    monkeypatch.setattr(model_download, "model_dir", lambda n: mdir)

    def _boom(*a, **k):
        raise AssertionError("fetch_file must not run — dir exists")

    monkeypatch.setattr(model_download, "fetch_file", _boom)
    model_download._download_deepfilternet3(None, None)


# ---- prefetch script -----------------------------------------------------------


def test_prefetch_core_models_incl_dfn(monkeypatch):
    """The M10 fix: deepfilternet3 was skipped while returning success."""
    import scripts.prefetch_models as pf

    assert "deepfilternet3" in pf.CORE_MODELS
    calls: list[str] = []
    monkeypatch.setattr(
        pf,
        "download_model",
        lambda m, **kw: calls.append(m) or {"model_id": m, "already": True},
    )
    monkeypatch.setattr(sys, "argv", ["prefetch_models.py"])
    assert pf.main() == 0
    assert calls == list(pf.CORE_MODELS)
    assert "deepfilternet3" in calls


def test_prefetch_all_models(monkeypatch):
    import scripts.prefetch_models as pf

    calls: list[str] = []
    monkeypatch.setattr(
        pf,
        "download_model",
        lambda m, **kw: calls.append(m) or {"model_id": m, "already": True},
    )
    monkeypatch.setattr(sys, "argv", ["prefetch_models.py", "--all"])
    assert pf.main() == 0
    assert calls == list(MODELS)


# ---- API ------------------------------------------------------------------------


def _client(tmp_path) -> TestClient:
    return make_client(
        create_app(Settings(data_dir=tmp_path, db_path=tmp_path / "app.db"))
    )


def test_models_list(tmp_path):
    c = _client(tmp_path)
    r = c.get("/api/models")
    assert r.status_code == 200, r.text
    models = {m["id"]: m for m in r.json()["models"]}
    assert set(models) == set(MODELS)
    for m in models.values():
        assert m["measured_bytes"] > 0
        assert m["approx_mb"] > 0
        assert m["downloading"] is False


def test_download_unknown_model_404(tmp_path):
    c = _client(tmp_path)
    r = c.post("/api/models/nope/download")
    assert r.status_code == 404


def test_download_bundled_model_is_done(tmp_path):
    """silero_vad ships in the wheel — never a download."""
    c = _client(tmp_path)
    r = c.post("/api/models/silero_vad/download")
    assert r.status_code == 202
    assert r.json()["state"] == "done"


_FAKE_CHILD = (
    "import sys,json\n"
    "def e(o): sys.stdout.write(json.dumps(o)+'\\n'); sys.stdout.flush()\n"
    "e({'type':'progress','pct':50.0,'file':'w.bin','done_bytes':5,'total_bytes':10})\n"
    "e({'type':'progress','pct':100.0,'file':'w.bin','done_bytes':10,'total_bytes':10})\n"
    "e({'type':'done','model_id':sys.argv[1] if len(sys.argv)>1 else ''})\n"
)


def test_download_task_progress_and_done(tmp_path, monkeypatch):
    """A stub child emits progress+done; the manager drains it and the
    task snapshots advance — no network involved."""
    import subprocess as sp

    c = _client(tmp_path)

    def _fake_spawn(self, task):
        from backend.api.models import ModelDownloadManager

        task.proc = sp.Popen(
            [sys.executable, "-c", _FAKE_CHILD],
            stdout=sp.PIPE, stderr=sp.STDOUT, stdin=sp.DEVNULL,
            text=True, encoding="utf-8", errors="replace",
        )
        threading.Thread(
            target=ModelDownloadManager._drain,
            args=(c.app.state.model_downloads, task), daemon=True,
        ).start()

    monkeypatch.setattr(
        "backend.api.models.ModelDownloadManager._spawn", _fake_spawn
    )
    # Force "not present" so POST actually starts a task.
    monkeypatch.setattr(
        "backend.api.models._present", lambda mid: False
    )
    r = c.post("/api/models/htdemucs/download")
    assert r.status_code == 202
    assert r.json()["state"] == "downloading"

    mgr = c.app.state.model_downloads
    snap = None
    for _ in range(200):
        snap = mgr.snapshot("htdemucs")
        if snap and snap["state"] != "downloading":
            break
        time.sleep(0.05)
    assert snap is not None
    assert snap["state"] == "done"
    assert snap["pct"] == 100.0


def test_download_cancel_kills_child(tmp_path, monkeypatch):
    import subprocess as sp

    c = _client(tmp_path)

    def _fake_spawn(self, task):
        from backend.api.models import ModelDownloadManager

        task.proc = sp.Popen(
            [
                sys.executable, "-c",
                "import time\n"
                "print('{\"type\":\"progress\",\"pct\":1}', flush=True)\n"
                "time.sleep(60)\n",
            ],
            stdout=sp.PIPE, stderr=sp.STDOUT, stdin=sp.DEVNULL,
            text=True, encoding="utf-8", errors="replace",
        )
        threading.Thread(
            target=ModelDownloadManager._drain,
            args=(c.app.state.model_downloads, task), daemon=True,
        ).start()

    monkeypatch.setattr(
        "backend.api.models.ModelDownloadManager._spawn", _fake_spawn
    )
    monkeypatch.setattr(
        "backend.api.models._present", lambda mid: False
    )
    c.post("/api/models/htdemucs/download")
    r = c.post("/api/models/htdemucs/download/cancel")
    assert r.status_code == 202
    assert r.json()["state"] == "cancelled"


def test_download_events_streams_done(tmp_path, monkeypatch):
    """SSE replays the last snapshot and finishes on a terminal state."""
    c = _client(tmp_path)
    monkeypatch.setattr(
        "backend.api.models._present", lambda mid: True
    )
    events: list[tuple[str, str]] = []
    with c.stream("GET", "/api/models/htdemucs/download/events") as r:
        ev = None
        for line in r.iter_lines():
            if line.startswith("event:"):
                ev = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                events.append((ev, line.split(":", 1)[1].strip()))
                if ev == "done":
                    break
    assert any(e == "done" for e, _ in events)
    payload = json.loads(events[-1][1])
    assert payload["state"] == "done"


# ---- T4: offline pipeline under a socket guard (slow) ----------------------------


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


def _collect_sse(c, jid: str, timeout: float = 900):
    got: list[tuple[str, object]] = []

    def _run():
        with c.stream("GET", f"/api/jobs/{jid}/events") as r:
            ev = None
            for line in r.iter_lines():
                if line.startswith("event:"):
                    ev = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    try:
                        payload = json.loads(line.split(":", 1)[1].strip())
                    except json.JSONDecodeError:
                        payload = {}
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


@pytest.mark.slow
def test_offline_pipeline(tmp_path, monkeypatch):
    """Every model-backed stage under a hard socket guard: the worker
    installs ``install_socket_guard`` (SOUND_SYSTEM_SOCKET_GUARD=1), so
    any real-world connect raises mid-run. A completed job = offline
    guarantee proven for whisper, audio-separator, DeepFilterNet and VAD
    (all already prefetched under models/)."""
    monkeypatch.setenv("SOUND_SYSTEM_SOCKET_GUARD", "1")
    # The worker inherits env — the flags are what production sets too.
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")

    c = _client(tmp_path)
    if not SPEECH.exists():
        pytest.skip("generated speech fixture missing")
    with c:
        mid = _upload(c, SPEECH)
        r = c.post(
            "/api/jobs",
            json={
                "media_id": mid,
                "device": "cpu",
                "ops": {
                    "highpass": {},
                    "denoise": {"engine": "ai", "strength_db": 24},
                    "separate": {"output": "vocals", "model": "htdemucs"},
                    "silence": {"action": "remove"},
                    "subtitles": {"model": "fast", "language": "auto"},
                    "loudness": {"target": "podcast"},
                },
                "output": {"audio_format": "wav", "keep_video": True},
            },
        )
        assert r.status_code == 201, r.text
        jid = r.json()["id"]
        events = _collect_sse(c, jid)
        final = next(
            p for e, p in reversed(events)
            if e == "done" and isinstance(p, dict)
        )
        assert final["state"] == "completed", final
