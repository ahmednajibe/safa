"""v0.1.1 server-side "Save as…" and "Save all to folder…" (SPEC §12).

``POST /api/jobs/{id}/files/{name}/save-as`` opens the native Save As
window in a short-lived subprocess and copies the output where the user
pointed; ``POST /api/jobs/{id}/save-all`` opens the folder window once
and copies every output in under non-colliding names. No test may open a
real dialog — ``run_save_as``/``run_picker`` (and ``_run_dialog``'s argv)
are the injection seams.
"""
import sys
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from backend.core import output_dir
from backend.core.output_dir import PickerUnavailable
from backend.core.settings import Settings
from backend.main import create_app
from backend.pipeline.types import OutputFile
from tests.client import make_client

GENERATED = Path(__file__).parent / "fixtures" / "generated"
ARABIC_WAV = GENERATED / "تسجيل اختبار 1.wav"

GOOD_ORIGIN = {"Origin": "http://127.0.0.1:8790"}


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


def _job_with_files(c, tmp_path, names=("out.wav",)) -> str:
    """A completed job whose outputs are real files on disk."""
    mid = _upload(c, ARABIC_WAV)
    jid = c.post(
        "/api/jobs", json={"media_id": mid, "device": "cpu"}
    ).json()["id"]
    store = c.app.state.store
    out_dir = tmp_path / "job_output"
    out_dir.mkdir(exist_ok=True)
    for name in names:
        p = out_dir / name
        p.write_bytes(b"AUDIO-" + name.encode("utf-8"))
        store.add_file(
            jid, OutputFile(name, "audio", p, p.stat().st_size)
        )
    store.finish(jid, "completed")
    return jid


def _save_as(c, jid, name="out.wav"):
    return c.post(
        f"/api/jobs/{jid}/files/{name}/save-as",
        json={},
        headers=GOOD_ORIGIN,
    )


def _save_all(c, jid):
    return c.post(f"/api/jobs/{jid}/save-all", json={}, headers=GOOD_ORIGIN)


# ---- POST /api/jobs/{id}/files/{name}/save-as ---------------------------------


def test_save_as_copies_bytes_and_records_path(
    tmp_path, fake_root, monkeypatch
):
    c = _client(tmp_path)
    jid = _job_with_files(c, tmp_path)
    chosen = tmp_path / "picked dir"
    chosen.mkdir()
    monkeypatch.setattr(
        output_dir, "run_save_as",
        lambda name: str(chosen / "renamed final.wav"),
    )
    r = _save_as(c, jid)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "saved"
    saved = Path(body["path"])
    assert saved.name == "renamed final.wav"
    assert saved.read_bytes() == b"AUDIO-out.wav"
    row = next(
        f for f in c.app.state.store.files(jid) if f["name"] == "out.wav"
    )
    assert row["saved_path"] == str(saved)


def test_save_as_cancelled(tmp_path, fake_root, monkeypatch):
    c = _client(tmp_path)
    jid = _job_with_files(c, tmp_path)
    monkeypatch.setattr(output_dir, "run_save_as", lambda name: None)
    r = _save_as(c, jid)
    assert r.status_code == 200
    assert r.json() == {"status": "cancelled"}
    assert c.app.state.store.files(jid)[0]["saved_path"] is None


def test_save_as_picker_unavailable_503(tmp_path, monkeypatch):
    c = _client(tmp_path)
    jid = _job_with_files(c, tmp_path)

    def boom(name):
        raise PickerUnavailable("no display")

    monkeypatch.setattr(output_dir, "run_save_as", boom)
    r = _save_as(c, jid)
    assert r.status_code == 503
    assert r.json()["code"] == "picker_unavailable"


def test_save_as_busy_409(tmp_path, monkeypatch):
    """A second native dialog while one is open → 409, and the dialog
    function must never run."""
    c = _client(tmp_path)
    jid = _job_with_files(c, tmp_path)
    monkeypatch.setattr(
        output_dir, "run_save_as",
        lambda name: pytest.fail("dialog ran"),
    )
    assert output_dir.PICK_LOCK.acquire(blocking=False)
    try:
        r = _save_as(c, jid)
        assert r.status_code == 409
        assert r.json()["code"] == "picker_busy"
    finally:
        output_dir.PICK_LOCK.release()
    # free again once the first dialog is done
    monkeypatch.setattr(output_dir, "run_save_as", lambda name: None)
    assert _save_as(c, jid).status_code == 200


def test_save_as_target_inside_repo_422(tmp_path, fake_root, monkeypatch):
    c = _client(tmp_path)
    jid = _job_with_files(c, tmp_path)
    inside = fake_root / "exports"
    inside.mkdir()
    monkeypatch.setattr(
        output_dir, "run_save_as", lambda name: str(inside / "out.wav")
    )
    r = _save_as(c, jid)
    assert r.status_code == 422
    assert r.json()["code"] == "output_dir_invalid"


def test_save_as_target_inside_data_dir_422(
    tmp_path, fake_root, monkeypatch
):
    """A save target inside the app data dir would nest exports next to
    app.db / the job store — refused like the settings folder."""
    from backend.core import paths

    c = _client(tmp_path)
    jid = _job_with_files(c, tmp_path)
    data = tmp_path / "safa-data"
    (data / "jobs").mkdir(parents=True)
    monkeypatch.setattr(paths, "DATA_DIR", data)
    monkeypatch.setattr(
        output_dir, "run_save_as",
        lambda name: str(data / "jobs" / "out.wav"),
    )
    r = _save_as(c, jid)
    assert r.status_code == 422
    assert r.json()["code"] == "output_dir_invalid"


def test_save_as_keeps_real_extension(tmp_path, fake_root, monkeypatch):
    """`out.mp3` chosen for a `.wav` output → `out.mp3.wav` — Save As never
    converts, so the file's real extension is appended."""
    c = _client(tmp_path)
    jid = _job_with_files(c, tmp_path)
    chosen = tmp_path / "picked"
    chosen.mkdir()
    monkeypatch.setattr(
        output_dir, "run_save_as", lambda name: str(chosen / "out.mp3")
    )
    r = _save_as(c, jid)
    assert r.status_code == 200, r.text
    saved = Path(r.json()["path"])
    assert saved.name == "out.mp3.wav"
    assert saved.read_bytes() == b"AUDIO-out.wav"


def test_save_as_overwrites_confirmed_target_no_tmp(
    tmp_path, fake_root, monkeypatch
):
    """The dialog already confirmed the overwrite — the existing file is
    replaced and no ``.<name>.<uuid>.tmp`` is left behind."""
    c = _client(tmp_path)
    jid = _job_with_files(c, tmp_path)
    chosen = tmp_path / "picked"
    chosen.mkdir()
    target = chosen / "out.wav"
    target.write_bytes(b"OLD-BYTES")
    monkeypatch.setattr(
        output_dir, "run_save_as", lambda name: str(target)
    )
    r = _save_as(c, jid)
    assert r.status_code == 200, r.text
    assert target.read_bytes() == b"AUDIO-out.wav"
    assert sorted(p.name for p in chosen.iterdir()) == ["out.wav"]


def test_save_as_arabic_folder_and_spaces(
    tmp_path, fake_root, monkeypatch
):
    c = _client(tmp_path)
    jid = _job_with_files(c, tmp_path)
    chosen = tmp_path / "مجلد الملفات الصوتية"
    chosen.mkdir()
    monkeypatch.setattr(
        output_dir, "run_save_as",
        lambda name: str(chosen / "النتيجة النهائية.wav"),
    )
    r = _save_as(c, jid)
    assert r.status_code == 200, r.text
    saved = Path(r.json()["path"])
    assert saved.name == "النتيجة النهائية.wav"
    assert saved.read_bytes() == b"AUDIO-out.wav"


def test_save_as_unknown_job_or_file_404(tmp_path):
    c = _client(tmp_path)
    jid = _job_with_files(c, tmp_path)
    r = _save_as(c, jid, "nope.wav")
    assert r.status_code == 404
    assert r.json()["code"] == "job_not_found"
    r = _save_as(c, "0" * 32)
    assert r.status_code == 404
    assert r.json()["code"] == "job_not_found"


# ---- POST /api/jobs/{id}/save-all ----------------------------------------------


def test_save_all_copies_every_file(tmp_path, fake_root, monkeypatch):
    c = _client(tmp_path)
    names = ("out.wav", "clip.mp4", "subs.srt")
    jid = _job_with_files(c, tmp_path, names)
    folder = tmp_path / "كل الملفات هنا"
    folder.mkdir()
    monkeypatch.setattr(output_dir, "run_picker", lambda: str(folder))
    r = _save_all(c, jid)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "saved"
    dest = Path(body["folder"])
    rows = {f["name"]: f for f in c.app.state.store.files(jid)}
    for name in names:
        copied = dest / name
        assert copied.read_bytes() == b"AUDIO-" + name.encode("utf-8")
        assert rows[name]["saved_path"] == str(copied)


def test_save_all_second_run_uses_2_names(tmp_path, fake_root, monkeypatch):
    """Saving twice into the same folder never overwrites: the second run
    lands as ``name (2).ext`` and saved_path moves to it."""
    c = _client(tmp_path)
    names = ("a.wav", "b.srt")
    jid = _job_with_files(c, tmp_path, names)
    folder = tmp_path / "saved"
    folder.mkdir()
    monkeypatch.setattr(output_dir, "run_picker", lambda: str(folder))
    assert _save_all(c, jid).json()["status"] == "saved"
    r = _save_all(c, jid)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "saved"
    found = sorted(p.name for p in folder.iterdir())
    assert found == ["a (2).wav", "a.wav", "b (2).srt", "b.srt"]
    rows = {f["name"]: f for f in c.app.state.store.files(jid)}
    assert Path(rows["a.wav"]["saved_path"]).name == "a (2).wav"
    assert Path(rows["b.srt"]["saved_path"]).name == "b (2).srt"
    assert (folder / "a (2).wav").read_bytes() == b"AUDIO-a.wav"


def test_save_all_cancelled(tmp_path, fake_root, monkeypatch):
    c = _client(tmp_path)
    jid = _job_with_files(c, tmp_path, ("a.wav", "b.srt"))
    monkeypatch.setattr(output_dir, "run_picker", lambda: None)
    r = _save_all(c, jid)
    assert r.status_code == 200
    assert r.json() == {"status": "cancelled"}
    assert all(
        f["saved_path"] is None for f in c.app.state.store.files(jid)
    )


def test_save_all_picker_unavailable_503(tmp_path, monkeypatch):
    c = _client(tmp_path)
    jid = _job_with_files(c, tmp_path, ("a.wav", "b.srt"))

    def boom():
        raise PickerUnavailable("no tk")

    monkeypatch.setattr(output_dir, "run_picker", boom)
    r = _save_all(c, jid)
    assert r.status_code == 503
    assert r.json()["code"] == "picker_unavailable"


def test_save_all_busy_409(tmp_path, monkeypatch):
    c = _client(tmp_path)
    jid = _job_with_files(c, tmp_path)
    monkeypatch.setattr(
        output_dir, "run_picker", lambda: pytest.fail("dialog ran")
    )
    assert output_dir.PICK_LOCK.acquire(blocking=False)
    try:
        r = _save_all(c, jid)
        assert r.status_code == 409
        assert r.json()["code"] == "picker_busy"
    finally:
        output_dir.PICK_LOCK.release()


def test_save_all_invalid_dir_422(tmp_path, fake_root, monkeypatch):
    c = _client(tmp_path)
    jid = _job_with_files(c, tmp_path)
    monkeypatch.setattr(
        output_dir, "run_picker", lambda: str(tmp_path / "missing")
    )
    r = _save_all(c, jid)
    assert r.status_code == 422
    assert r.json()["code"] == "output_dir_invalid"


def test_save_all_picked_drive_root_accepted(
    tmp_path, fake_root, monkeypatch
):
    """A drive root the user explicitly picked is a valid Save-all
    destination — like Save As, the endpoint must pass
    ``allow_drive_root=True`` to ``validate_output_dir``."""
    c = _client(tmp_path)
    jid = _job_with_files(c, tmp_path, ("a.wav",))
    dest = tmp_path / "dest"
    dest.mkdir()
    calls = {}

    def spy(raw, **kwargs):
        calls["raw"] = raw
        calls.update(kwargs)
        return dest

    picked = str(Path(tmp_path.anchor))
    monkeypatch.setattr(output_dir, "run_picker", lambda: picked)
    monkeypatch.setattr(output_dir, "validate_output_dir", spy)
    r = _save_all(c, jid)
    assert r.status_code == 200, r.text
    assert calls["raw"] == picked
    assert calls["allow_drive_root"] is True
    assert r.json()["folder"] == str(dest)
    assert (dest / "a.wav").read_bytes() == b"AUDIO-a.wav"


def test_save_all_unknown_job_404(tmp_path):
    c = _client(tmp_path)
    assert _save_all(c, "nope").status_code == 404
    assert _save_all(c, "0" * 32).status_code == 404


# ---- dialog plumbing ------------------------------------------------------------


def test_run_dialog_decodes_utf8_path():
    """An Arabic choice arrives over stdout as UTF-8 — the parent decodes
    it (the ANSI code page would mangle it)."""
    cmd = [
        sys.executable, "-c",
        "import sys; sys.stdout.reconfigure(encoding='utf-8');"
        " sys.stdout.write('C:/مجلد/x.wav')",
    ]
    assert output_dir._run_dialog(cmd, 30) == "C:/مجلد/x.wav"


def test_validate_output_dir_case_only_difference(tmp_path):
    """Windows: a path differing only in letter case resolves to the same
    on-disk dir — normcase must not flag it as a link escape."""
    if sys.platform != "win32":
        pytest.skip("case-insensitive filesystems are a Windows thing")
    d = tmp_path / "MiXeD Case Dir"
    d.mkdir()
    got = output_dir.validate_output_dir(
        str(d).lower(), project_root=tmp_path / "repo"
    )
    assert got == d.resolve()
