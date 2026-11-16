"""M9a Task 0 item 6: saving backend (SPEC §12).

settings table; POST /api/settings/output-dir/pick (native tkinter dialog in
a short-lived subprocess — injectable command, single-flight 409, timeout),
PUT/DELETE /api/settings/output-dir with validation, atomic non-colliding
auto-copy (never previews, never fails the job), job_files.saved_path, and
POST /api/jobs/{id}/files/{name}/reveal (explorer /select — mocked; tests
must never open a real dialog or File Explorer).
"""
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

import backend.api.settings as settings_api
from backend.core import output_dir
from backend.core.output_dir import (
    OutputDirInvalid,
    PickerUnavailable,
)
from backend.core.settings import Settings
from backend.db import init_db
from backend.jobs.store import JobStore
from backend.main import create_app
from backend.media.probe import probe
from backend.pipeline.types import OutputFile
from backend.worker import main as worker_main
from tests.client import make_client

GENERATED = Path(__file__).parent / "fixtures" / "generated"
ARABIC_WAV = GENERATED / "تسجيل اختبار 1.wav"

GOOD_ORIGIN = {"Origin": "http://127.0.0.1:8790"}
EVIL_ORIGIN = {"Origin": "http://evil.example"}
CROSS_FETCH = {"Sec-Fetch-Site": "cross-site"}


def _client(tmp_path) -> TestClient:
    return make_client(
        create_app(Settings(data_dir=tmp_path, db_path=tmp_path / "app.db"))
    )


@pytest.fixture()
def fake_root(tmp_path, monkeypatch):
    """Point the project-root blocklist at a fake repo inside tmp so the
    (also tmp) "picked" folder counts as outside it."""
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.setattr(output_dir, "ROOT", root)
    return root


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


def _new_dir(tmp_path, name="saved") -> Path:
    d = tmp_path / name
    d.mkdir()
    return d


# ---- validate_output_dir -----------------------------------------------------


def test_validate_ok_existing_writable_dir(tmp_path):
    d = _new_dir(tmp_path)
    got = output_dir.validate_output_dir(
        str(d), project_root=tmp_path / "repo"
    )
    assert got == d.resolve()


def test_validate_strips_quotes_and_spaces(tmp_path):
    d = _new_dir(tmp_path)
    got = output_dir.validate_output_dir(
        f'  "{d}"  ', project_root=tmp_path / "repo"
    )
    assert got == d.resolve()


def test_validate_rejects_missing_file_and_empty(tmp_path):
    root = tmp_path / "repo"
    with pytest.raises(OutputDirInvalid):
        output_dir.validate_output_dir(
            str(tmp_path / "nope"), project_root=root
        )
    f = tmp_path / "afile.bin"
    f.write_bytes(b"x")
    with pytest.raises(OutputDirInvalid):
        output_dir.validate_output_dir(str(f), project_root=root)
    with pytest.raises(OutputDirInvalid):
        output_dir.validate_output_dir("   ", project_root=root)


def test_validate_rejects_drive_root(tmp_path):
    root = tmp_path / "repo"
    drive = Path(tmp_path.drive + "/") if tmp_path.drive else Path("/")
    with pytest.raises(OutputDirInvalid):
        output_dir.validate_output_dir(str(drive), project_root=root)


def test_validate_rejects_inside_project_root(tmp_path):
    root = _new_dir(tmp_path, "repo")
    inside = root / "sub" / "dir"
    inside.mkdir(parents=True)
    for p in (inside, root):
        with pytest.raises(OutputDirInvalid):
            output_dir.validate_output_dir(str(p), project_root=root)


def test_validate_rejects_data_and_models_dir(tmp_path, monkeypatch):
    """R2 #2: installed-mode DATA_DIR/MODELS_DIR live outside the repo —
    auto-saving exports into them would nest outputs next to app.db and
    the model cache."""
    from backend.core import paths

    root = _new_dir(tmp_path, "repo")
    data = _new_dir(tmp_path, "safa-data")
    models = _new_dir(_new_dir(tmp_path, "elsewhere"), "models")
    monkeypatch.setattr(paths, "DATA_DIR", data)
    monkeypatch.setattr(paths, "MODELS_DIR", models)
    (data / "sub").mkdir()
    (models / "whisper").mkdir()
    for p in (data, data / "sub", models, models / "whisper"):
        with pytest.raises(OutputDirInvalid):
            output_dir.validate_output_dir(str(p), project_root=root)


def test_validate_rejects_symlink_junction_escape(tmp_path):
    """A junction/symlink resolves elsewhere — resolve() changes the
    path → rejected (SPEC §12 'symlink/junction escapes')."""
    target = _new_dir(tmp_path, "real_target")
    link = tmp_path / "link_dir"
    if sys.platform == "win32":
        cp = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
        )
        if cp.returncode != 0:
            pytest.skip("cannot create junction")
    else:
        try:
            os.symlink(target, link)
        except OSError:
            pytest.skip("cannot create symlink")
    assert link.is_dir()  # the link itself works — the check must refuse it
    with pytest.raises(OutputDirInvalid):
        output_dir.validate_output_dir(
            str(link), project_root=tmp_path / "repo"
        )


def test_validate_rejects_unwritable_dir(tmp_path):
    """A folder the user can't write into must be refused. On Windows the
    dir's read-only *attribute* doesn't stop file creation — an ACL deny
    does."""
    if sys.platform != "win32":
        pytest.skip("icacls deny-write is a Windows thing")
    who = os.environ.get("USERNAME")
    if not who:
        pytest.skip("no USERNAME for icacls")
    dom = os.environ.get("USERDOMAIN")
    if dom:
        who = f"{dom}\\{who}"  # ACEs are stored domain-qualified
    d = _new_dir(tmp_path)
    cp = subprocess.run(
        ["icacls", str(d), "/deny", f"{who}:(OI)(CI)W"],
        capture_output=True,
    )
    if cp.returncode != 0:
        pytest.skip("cannot deny write via icacls")
    try:
        with pytest.raises(OutputDirInvalid):
            output_dir.validate_output_dir(
                str(d), project_root=tmp_path / "repo"
            )
    finally:
        subprocess.run(
            ["icacls", str(d), "/remove:d", who],
            capture_output=True,
        )


# ---- store settings KV ---------------------------------------------------------


def test_store_settings_roundtrip(tmp_path):
    db = tmp_path / "app.db"
    init_db(db)
    store = JobStore(db)
    assert store.get_setting("output_dir") is None
    store.set_setting("output_dir", "D:/Results")
    assert store.get_setting("output_dir") == "D:/Results"
    store.set_setting("output_dir", "E:/Other")
    assert store.get_setting("output_dir") == "E:/Other"
    store.delete_setting("output_dir")
    assert store.get_setting("output_dir") is None


# ---- settings API ----------------------------------------------------------------


def test_get_settings_initially_null(tmp_path):
    r = _client(tmp_path).get("/api/settings")
    assert r.status_code == 200
    assert r.json() == {"output_dir": None, "retention_days": 7}


def test_put_output_dir_valid_roundtrip(tmp_path, fake_root):
    c = _client(tmp_path)
    d = _new_dir(tmp_path)
    r = c.put(
        "/api/settings/output-dir",
        json={"path": str(d)},
        headers=GOOD_ORIGIN,
    )
    assert r.status_code == 200, r.text
    assert r.json()["output_dir"] == str(d.resolve())
    assert r.json()["message_code"] == "output_dir_set"
    assert c.get("/api/settings").json()["output_dir"] == str(d.resolve())


def test_put_output_dir_invalid_422(tmp_path, fake_root):
    c = _client(tmp_path)
    for bad in (
        str(tmp_path / "missing"),
        str(fake_root),  # the project root itself
        str(fake_root / "sub"),  # inside the project
    ):
        r = c.put(
            "/api/settings/output-dir",
            json={"path": bad},
            headers=GOOD_ORIGIN,
        )
        assert r.status_code == 422, bad
        assert r.json()["code"] == "output_dir_invalid"


def test_put_output_dir_drive_root_422(tmp_path, fake_root):
    c = _client(tmp_path)
    drive = Path(tmp_path.drive + "/") if tmp_path.drive else Path("/")
    r = c.put(
        "/api/settings/output-dir",
        json={"path": str(drive)},
        headers=GOOD_ORIGIN,
    )
    assert r.status_code == 422
    assert r.json()["code"] == "output_dir_invalid"


def test_delete_output_dir(tmp_path, fake_root):
    c = _client(tmp_path)
    d = _new_dir(tmp_path)
    c.put(
        "/api/settings/output-dir",
        json={"path": str(d)},
        headers=GOOD_ORIGIN,
    )
    r = c.delete("/api/settings/output-dir", headers=GOOD_ORIGIN)
    assert r.status_code == 204
    assert c.get("/api/settings").json()["output_dir"] is None


# ---- the native picker (command injected — no real dialog) ----------------------


def _say_dir(path: Path) -> list[str]:
    return [sys.executable, "-c", f"import sys; sys.stdout.write({str(path)!r})"]


def test_pick_returns_and_stores_dir(tmp_path, fake_root, monkeypatch):
    c = _client(tmp_path)
    d = _new_dir(tmp_path)
    monkeypatch.setattr(
        output_dir, "build_picker_command", lambda: _say_dir(d)
    )
    r = c.post("/api/settings/output-dir/pick", headers=GOOD_ORIGIN)
    assert r.status_code == 200, r.text
    assert r.json()["output_dir"] == str(d.resolve())
    assert r.json()["cancelled"] is False
    assert c.app.state.store.get_setting("output_dir") == str(d.resolve())


def test_pick_cancelled_keeps_previous(tmp_path, fake_root, monkeypatch):
    c = _client(tmp_path)
    c.app.state.store.set_setting("output_dir", "D:/kept")
    monkeypatch.setattr(
        output_dir, "build_picker_command",
        lambda: [sys.executable, "-c", "import sys; sys.stdout.write('')"],
    )
    r = c.post("/api/settings/output-dir/pick", headers=GOOD_ORIGIN)
    assert r.status_code == 200
    assert r.json()["cancelled"] is True
    assert c.app.state.store.get_setting("output_dir") == "D:/kept"


def test_pick_single_flight_409(tmp_path):
    """While one folder dialog is open (lock held) a second POST must get
    409 — never a second dialog process."""
    c = _client(tmp_path)
    assert settings_api._PICK_LOCK.acquire(blocking=False)
    try:
        r = c.post("/api/settings/output-dir/pick", headers=GOOD_ORIGIN)
        assert r.status_code == 409
        assert r.json()["code"] == "picker_busy"
    finally:
        settings_api._PICK_LOCK.release()
    # and it works again once the dialog is done
    r = c.post("/api/settings/output-dir/pick", headers=GOOD_ORIGIN)
    assert r.status_code != 409


def test_pick_unavailable_503(tmp_path, monkeypatch):
    c = _client(tmp_path)
    # dialog process fails to start at all → OSError
    monkeypatch.setattr(
        output_dir, "build_picker_command",
        lambda: ["no-such-executable-xyz-123"],
    )
    r = c.post("/api/settings/output-dir/pick", headers=GOOD_ORIGIN)
    assert r.status_code == 503
    assert r.json()["code"] == "picker_unavailable"
    # …or it starts but Tk/desktop is missing → non-zero exit
    monkeypatch.setattr(
        output_dir, "build_picker_command",
        lambda: [sys.executable, "-c", "import sys; sys.exit(3)"],
    )
    r = c.post("/api/settings/output-dir/pick", headers=GOOD_ORIGIN)
    assert r.status_code == 503
    assert r.json()["code"] == "picker_unavailable"


def test_pick_timeout_is_a_cancel(tmp_path, monkeypatch):
    """The 120 s guard kills a hung dialog; from the UI's side that is
    just "nothing was chosen"."""
    c = _client(tmp_path)
    monkeypatch.setattr(output_dir, "PICKER_TIMEOUT_S", 0.5)
    monkeypatch.setattr(
        output_dir, "build_picker_command",
        lambda: [sys.executable, "-c", "import time; time.sleep(30)"],
    )
    r = c.post("/api/settings/output-dir/pick", headers=GOOD_ORIGIN)
    assert r.status_code == 200
    assert r.json()["cancelled"] is True


def test_pick_invalid_dir_422(tmp_path, fake_root, monkeypatch):
    c = _client(tmp_path)
    missing = tmp_path / "missing"
    monkeypatch.setattr(
        output_dir, "build_picker_command", lambda: _say_dir(missing)
    )
    r = c.post("/api/settings/output-dir/pick", headers=GOOD_ORIGIN)
    assert r.status_code == 422
    assert r.json()["code"] == "output_dir_invalid"


def test_picker_script_is_dpi_aware_topmost_and_hidden():
    """The real command script must carry the window flags SPEC §12
    demands — asserted on the script text, never actually opened."""
    cmd = output_dir.build_picker_command()
    assert cmd[:2] == [sys.executable, "-c"]
    script = cmd[2]
    assert "SetProcessDpiAwarenessContext(-4)" in script
    assert script.index("SetProcessDpiAwarenessContext") < script.index(
        "Tk()"
    ), "DPI awareness must be set before Tk()"
    assert "withdraw" in script
    assert '-topmost", 1' in script or "-topmost', 1" in script
    assert "askdirectory" in script


# ---- auto-save copy --------------------------------------------------------------


def _job_with_file(store, tmp_path, name="song_clean.mp3"):
    """One queued job + one real output file on disk, registered."""
    job = store.create("m1", "full", [], {}, None, "cpu", 0)
    payload = tmp_path / "job_out" / name
    payload.parent.mkdir(parents=True, exist_ok=True)
    payload.write_bytes(b"AUDIODATA")
    f = OutputFile(name, "audio", payload, 9)
    store.add_file(job.id, f)
    return job, [f]


def test_auto_save_copies_and_records(tmp_path):
    db = tmp_path / "app.db"
    init_db(db)
    store = JobStore(db)
    _insert_media(db)
    target = _new_dir(tmp_path)
    store.set_setting("output_dir", str(target))
    job, files = _job_with_file(store, tmp_path)

    output_dir.auto_save_outputs(store, job.id, "full", files)

    copied = target / "song_clean.mp3"
    assert copied.read_bytes() == b"AUDIODATA"
    row = store.files(job.id)[0]
    assert row["saved_path"] == str(copied)
    job = store.get(job.id)
    assert any(
        w["warning_code"] == "saved_to"
        and w["warning_params"]["path"] == str(target)
        for w in job.warnings
    )


def test_auto_save_never_overwrites_existing(tmp_path):
    db = tmp_path / "app.db"
    init_db(db)
    _insert_media(db)
    store = JobStore(db)
    target = _new_dir(tmp_path)
    (target / "song_clean.mp3").write_bytes(b"ORIGINAL")
    store.set_setting("output_dir", str(target))
    job, files = _job_with_file(store, tmp_path)

    output_dir.auto_save_outputs(store, job.id, "full", files)

    assert (target / "song_clean.mp3").read_bytes() == b"ORIGINAL"
    new_copy = target / "song_clean (2).mp3"
    assert new_copy.read_bytes() == b"AUDIODATA"
    assert store.files(job.id)[0]["saved_path"] == str(new_copy)


def test_auto_copy_concurrent_same_name(tmp_path):
    """R2 #5: two workers auto-saving the same file name at once must not
    overwrite each other — the name is claimed with O_EXCL, not probed."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    n = 8
    dst = _new_dir(tmp_path)
    srcs = []
    for i in range(n):
        s = tmp_path / f"src{i}" / "mix.wav"
        s.parent.mkdir()
        s.write_bytes(f"chunk-{i}".encode() * 131072)  # ~1 MB per copy
        srcs.append(s)
    bar = threading.Barrier(n)

    def go(s):
        bar.wait()
        return output_dir._auto_copy(s, dst)

    with ThreadPoolExecutor(max_workers=n) as ex:
        targets = list(ex.map(go, srcs))

    names = [t.name for t in targets]
    assert len(set(names)) == n  # every copier got a distinct name
    for s, t in zip(srcs, targets):
        assert t.read_bytes() == s.read_bytes()
    assert sorted(p.name for p in dst.iterdir()) == sorted(names)
    assert "mix.wav" in names and "mix (8).wav" in names


def test_auto_save_missing_folder_warns_not_fails(tmp_path):
    db = tmp_path / "app.db"
    init_db(db)
    _insert_media(db)
    store = JobStore(db)
    store.set_setting("output_dir", str(tmp_path / "gone"))
    job, files = _job_with_file(store, tmp_path)

    output_dir.auto_save_outputs(store, job.id, "full", files)  # no raise

    job = store.get(job.id)
    assert any(
        w["warning_code"] == "auto_save_failed" for w in job.warnings
    )
    assert store.files(job.id)[0]["saved_path"] is None


def test_auto_save_no_space_warns(tmp_path, monkeypatch):
    db = tmp_path / "app.db"
    init_db(db)
    _insert_media(db)
    store = JobStore(db)
    target = _new_dir(tmp_path)
    store.set_setting("output_dir", str(target))
    monkeypatch.setattr(output_dir, "free_space_bytes", lambda p: 1)
    job, files = _job_with_file(store, tmp_path)

    output_dir.auto_save_outputs(store, job.id, "full", files)

    assert any(
        w["warning_code"] == "auto_save_failed"
        for w in store.get(job.id).warnings
    )
    # no half-written copy and no stray tmp files
    assert [p.name for p in target.iterdir()] == []


def test_auto_save_skips_preview_and_no_setting(tmp_path):
    db = tmp_path / "app.db"
    init_db(db)
    _insert_media(db)
    store = JobStore(db)
    target = _new_dir(tmp_path)
    store.set_setting("output_dir", str(target))
    job, files = _job_with_file(store, tmp_path)

    output_dir.auto_save_outputs(store, job.id, "preview", files)
    assert [p.name for p in target.iterdir()] == []
    assert store.files(job.id)[0]["saved_path"] is None

    store.delete_setting("output_dir")
    output_dir.auto_save_outputs(store, job.id, "full", files)
    assert [p.name for p in target.iterdir()] == []
    assert not store.get(job.id).warnings


def _insert_media(db, media_id="m1"):
    from backend.db import connect

    con = connect(db)
    try:
        con.execute(
            "INSERT OR IGNORE INTO media (id, filename, stored_path,"
            " size_bytes, probe_json, created_at) VALUES (?,?,?,?,?,?)",
            (media_id, "x.wav", "/x.wav", 1, "{}", "2026-11-15T00:00:00"),
        )
        con.commit()
    finally:
        con.close()


# ---- reveal in folder -----------------------------------------------------------


def _finished_job_with_saved_file(c, tmp_path, saved_dir=None):
    mid = _upload(c, ARABIC_WAV)
    jid = c.post(
        "/api/jobs", json={"media_id": mid, "device": "cpu"}
    ).json()["id"]
    store = c.app.state.store
    payload = tmp_path / "out.mp3"
    payload.write_bytes(b"0123")
    store.add_file(jid, OutputFile("out.mp3", "audio", payload, 4))
    if saved_dir is not None:
        saved = saved_dir / "out.mp3"
        saved.write_bytes(b"0123")
        store.set_saved_path(jid, "out.mp3", saved)
    store.finish(jid, "completed")
    return jid


def test_reveal_calls_explorer_on_saved_path(tmp_path, monkeypatch):
    c = _client(tmp_path)
    saved_dir = _new_dir(tmp_path, "saved")
    jid = _finished_job_with_saved_file(c, tmp_path, saved_dir)
    calls = []
    monkeypatch.setattr(
        output_dir, "reveal_in_explorer", lambda p: calls.append(Path(p))
    )
    r = c.post(
        f"/api/jobs/{jid}/files/out.mp3/reveal", headers=GOOD_ORIGIN
    )
    assert r.status_code == 200, r.text
    assert calls == [saved_dir / "out.mp3"]


def test_reveal_404_without_saved_path(tmp_path, monkeypatch):
    c = _client(tmp_path)
    jid = _finished_job_with_saved_file(c, tmp_path)
    calls = []
    monkeypatch.setattr(
        output_dir, "reveal_in_explorer", lambda p: calls.append(p)
    )
    r = c.post(
        f"/api/jobs/{jid}/files/out.mp3/reveal", headers=GOOD_ORIGIN
    )
    assert r.status_code == 404
    assert r.json()["code"] == "no_saved_path"
    assert calls == []


def test_reveal_404_saved_file_gone(tmp_path, monkeypatch):
    c = _client(tmp_path)
    mid = _upload(c, ARABIC_WAV)
    jid = c.post(
        "/api/jobs", json={"media_id": mid, "device": "cpu"}
    ).json()["id"]
    store = c.app.state.store
    payload = tmp_path / "out.mp3"
    payload.write_bytes(b"0123")
    store.add_file(jid, OutputFile("out.mp3", "audio", payload, 4))
    store.set_saved_path(jid, "out.mp3", tmp_path / "gone" / "out.mp3")
    calls = []
    monkeypatch.setattr(
        output_dir, "reveal_in_explorer", lambda p: calls.append(p)
    )
    r = c.post(
        f"/api/jobs/{jid}/files/out.mp3/reveal", headers=GOOD_ORIGIN
    )
    assert r.status_code == 404
    assert calls == []


def test_reveal_404_unknown_job_or_file(tmp_path):
    c = _client(tmp_path)
    assert c.post(
        "/api/jobs/nope/files/out.mp3/reveal", headers=GOOD_ORIGIN
    ).status_code == 404
    mid = _upload(c, ARABIC_WAV)
    jid = c.post(
        "/api/jobs", json={"media_id": mid, "device": "cpu"}
    ).json()["id"]
    assert c.post(
        f"/api/jobs/{jid}/files/out.mp3/reveal", headers=GOOD_ORIGIN
    ).status_code == 404


def test_reveal_command_is_explorer_select(monkeypatch):
    """The explorer call must be an arg list ``explorer.exe /select,<p>``
    — inspect the argv without launching anything."""
    argv = {}

    class _FakePopen:
        def __init__(self, args, **kw):
            argv["args"] = args

    monkeypatch.setattr(subprocess, "Popen", _FakePopen)
    output_dir.reveal_in_explorer(Path("D:/my dir/out.mp3"))
    args = argv["args"]
    assert args[0] == "explorer.exe"
    assert args[1].startswith("/select,")
    assert "D:\\my dir\\out.mp3" in args[1] or "D:/my dir/out.mp3" in args[1]


# ---- CSRF: Origin/Referer on the new state-changing endpoints ---------------------


def test_origin_check_on_new_endpoints(tmp_path, monkeypatch):
    """R1 A1 middleware must cover the picker, settings PUT/DELETE and
    reveal: cross-site Origin → 403 bad_origin (they can spawn dialogs /
    write files, so they must not be callable from another tab's page)."""
    c = _client(tmp_path)
    monkeypatch.setattr(
        output_dir, "run_picker", lambda *a, **kw: pytest.fail("picker ran")
    )
    monkeypatch.setattr(
        output_dir, "reveal_in_explorer",
        lambda p: pytest.fail("explorer ran"),
    )
    mid = _upload(c, ARABIC_WAV)
    jid = c.post(
        "/api/jobs", json={"media_id": mid, "device": "cpu"}
    ).json()["id"]

    for method, url, kwargs in (
        ("post", "/api/settings/output-dir/pick", {}),
        ("put", "/api/settings/output-dir", {"json": {"path": "D:/x"}}),
        ("delete", "/api/settings/output-dir", {}),
        ("post", f"/api/jobs/{jid}/files/out.mp3/reveal", {}),
    ):
        r = getattr(c, method)(url, headers=EVIL_ORIGIN, **kwargs)
        assert r.status_code == 403, (method, url, r.status_code)
        assert r.json()["code"] == "bad_origin"
        r = getattr(c, method)(url, headers=CROSS_FETCH, **kwargs)
        assert r.status_code == 403, (method, url, "sec-fetch-site")


def test_origin_same_site_accepted(tmp_path, monkeypatch):
    """A same-origin mutation reaches the handler (not a 403)."""
    c = _client(tmp_path)
    monkeypatch.setattr(output_dir, "run_picker", lambda *a, **kw: None)
    r = c.post("/api/settings/output-dir/pick", headers=GOOD_ORIGIN)
    assert r.status_code == 200


# ---- worker end-to-end auto-save ---------------------------------------------------


def _setup(tmp_path, monkeypatch):
    db = tmp_path / "app.db"
    data = tmp_path / "data"
    data.mkdir()
    init_db(db)
    monkeypatch.setenv("SOUND_SYSTEM_DB", str(db))
    monkeypatch.setenv("SOUND_SYSTEM_DATA", str(data))
    return db, data


def _media(db, media_id, path: Path):
    from backend.db import connect

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


def test_worker_auto_saves_completed_outputs(tmp_path, monkeypatch):
    db, data = _setup(tmp_path, monkeypatch)
    _media(db, "m1", ARABIC_WAV)
    store = JobStore(db)
    target = _new_dir(tmp_path, "saved")
    store.set_setting("output_dir", str(target))
    job = store.create(
        media_id="m1", kind="full", ops=[],
        output={"audio_format": "mp3", "keep_video": False},
        preview=None, device="cpu", audio_stream=0,
    )

    assert worker_main([job.id]) == 0

    got = store.get(job.id)
    assert got.state == "completed"
    files = store.files(job.id)
    assert len(files) == 1
    saved = Path(files[0]["saved_path"])
    assert saved.parent == target
    assert saved.read_bytes() == Path(files[0]["path"]).read_bytes()
    assert any(
        w["warning_code"] == "saved_to" for w in got.warnings
    )


def test_worker_preview_job_not_auto_saved(tmp_path, monkeypatch):
    db, data = _setup(tmp_path, monkeypatch)
    _media(db, "m1", ARABIC_WAV)
    store = JobStore(db)
    target = _new_dir(tmp_path, "saved")
    store.set_setting("output_dir", str(target))
    job = store.create(
        media_id="m1", kind="preview", ops=[],
        output={"audio_format": "wav", "keep_video": False},
        preview={"start_s": 0.5, "end_s": 2.5},
        device="cpu", audio_stream=0,
    )

    assert worker_main([job.id]) == 0
    assert store.get(job.id).state == "completed"
    assert [p.name for p in target.iterdir()] == []
    assert store.files(job.id)[0]["saved_path"] is None


def test_worker_auto_save_failure_still_completes(tmp_path, monkeypatch):
    """Output dir vanishes mid-run → warning, but the job is completed."""
    db, data = _setup(tmp_path, monkeypatch)
    _media(db, "m1", ARABIC_WAV)
    store = JobStore(db)
    store.set_setting("output_dir", str(tmp_path / "removed"))
    job = store.create(
        media_id="m1", kind="full", ops=[],
        output={"audio_format": "wav", "keep_video": False},
        preview=None, device="cpu", audio_stream=0,
    )

    assert worker_main([job.id]) == 0
    got = store.get(job.id)
    assert got.state == "completed"
    assert any(
        w["warning_code"] == "auto_save_failed" for w in got.warnings
    )
    # outputs are still served from the app copy
    files = store.files(job.id)
    assert files and files[0]["saved_path"] is None
