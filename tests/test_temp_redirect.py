"""M10 T2 (keep C: clean): the app, its workers and its tools point
``TEMP``/``TMP``/``TMPDIR`` at ``<data>/tmp`` — a full pipeline run on an
Arabic + space path must not leave new files in the system %TEMP%."""
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from backend.core.paths import ROOT, redirect_temp_env, tmp_dir
from backend.core.settings import Settings
from backend.db import connect, init_db
from backend.jobs.store import JobStore
from backend.main import create_app
from backend.media.probe import probe
from tests.client import make_client


def test_tmp_dir_and_redirect(tmp_path, monkeypatch):
    for var in ("TEMP", "TMP", "TMPDIR"):
        monkeypatch.setenv(var, str(tmp_path / "system-temp"))
    d = redirect_temp_env(tmp_path / "data")
    assert d == tmp_path / "data" / "tmp"
    assert d.is_dir()
    for var in ("TEMP", "TMP", "TMPDIR"):
        assert os.environ[var] == str(d)


def test_create_app_redirects_temp(tmp_path, monkeypatch):
    for var in ("TEMP", "TMP", "TMPDIR"):
        monkeypatch.setenv(var, str(tmp_path / "system-temp"))
    create_app(
        Settings(
            data_dir=tmp_path / "data",
            db_path=tmp_path / "data" / "app.db",
        )
    )
    for var in ("TEMP", "TMP", "TMPDIR"):
        assert os.environ[var] == str(tmp_path / "data" / "tmp")


def test_no_system_temp_leak(tmp_path, monkeypatch):
    """Full pipeline via a real worker subprocess on an Arabic + space
    media path leaves zero new files in the system %TEMP%.

    R2 #4 (deterministic): instead of diffing the real %TEMP% — where any
    unrelated process can drop a file mid-run — the test points
    TEMP/TMP/TMPDIR at a sentinel dir it owns, then asserts the app and
    its worker redirect everything to ``<data>/tmp`` and never touch the
    sentinel. A foreign file written to the real %TEMP% mid-run proves
    the assertion no longer depends on what other processes do."""
    real_temp = Path(
        os.environ.get("TEMP") or tempfile.gettempdir()
    ).resolve()
    sentinel = tmp_path / "system-temp-sentinel"
    sentinel.mkdir()
    for var in ("TEMP", "TMP", "TMPDIR"):
        monkeypatch.setenv(var, str(sentinel))

    data = tmp_path / "data"
    app = create_app(
        Settings(data_dir=data, db_path=data / "app.db")
    )
    store: JobStore = app.state.store

    # An Arabic + space media path (hard rule: no shell-built strings).
    src_dir = tmp_path / "ملفات صوتية جديدة"
    src_dir.mkdir()
    src = src_dir / "تسجيل اختبار عربي.wav"
    t = np.arange(0, 2.0, 1 / 48000)
    tone = (0.2 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    sf.write(src, np.column_stack([tone, tone]), 48000)
    p = probe(src)

    media_id = "a" * 32
    con = connect(data / "app.db")
    try:
        con.execute(
            "INSERT INTO media (id, filename, stored_path, size_bytes,"
            " probe_json, created_at) VALUES (?,?,?,?,?,?)",
            (
                media_id,
                "تسجيل اختبار عربي.wav",
                str(src),
                src.stat().st_size,
                json.dumps(p.to_dict()),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        con.commit()
    finally:
        con.close()

    job = store.create(
        media_id=media_id,
        kind="full",
        ops=[{"id": "loudness", "params": {}}],
        output={"audio_format": "wav", "keep_video": False},
        preview=None,
        device="cpu",
        audio_stream=0,
    )
    from backend.media.storage import job_dir

    job_root = job_dir(job.id, data)
    foreign = real_temp / f"safa_foreign_{os.getpid()}.tmp"
    foreign.write_bytes(b"not ours")  # self-test: must not flake the run
    logf = (job_root / "worker.log").open("ab")
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "backend.worker", job.id],
            cwd=str(ROOT),
            env=app.state.supervisor.worker_env(job),
            stdout=logf,
            stderr=subprocess.STDOUT,
            timeout=180,
        )
    finally:
        logf.close()
        foreign.unlink(missing_ok=True)
    assert proc.returncode == 0, (job_root / "worker.log").read_text(
        encoding="utf-8", errors="replace"
    )[-2000:]
    assert store.get(job.id).state == "completed"

    # The worker's temp world is <data>/tmp — prove a child of the
    # supervisor env resolves tempfile there.
    out = subprocess.run(
        [
            sys.executable, "-c",
            "import tempfile; print(tempfile.gettempdir())",
        ],
        env=app.state.supervisor.worker_env(job),
        capture_output=True, text=True, cwd=str(ROOT),
    )
    child_tmp = Path(out.stdout.strip()).resolve()
    assert child_tmp == (data / "tmp").resolve(), out.stdout

    # The sentinel is what the OS called temp for this whole run — the
    # app + worker must have redirected everything to <data>/tmp.
    leaked = sorted(p.name for p in sentinel.iterdir())
    assert not leaked, f"app wrote into system TEMP: {leaked[:8]}"
