"""R3 §3: first-run desktop shortcut (Wird-style, done in Python).

``backend.core.shortcut.maybe_create_desktop_shortcut`` fires once per
install (marker ``<data>/.desktop-shortcut``), only on Windows, never in
installed/Docker/test mode, and can never break startup. Every
subprocess call is a fake — tests must NEVER spawn real PowerShell or
touch the real Desktop (conftest sets SAFA_NO_SHORTCUT=1 session-wide).
"""
from __future__ import annotations

import subprocess
import sys
import types
from pathlib import Path

import pytest

from backend.core import shortcut


# ---- helpers --------------------------------------------------------------------


@pytest.fixture
def win32(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv("SAFA_NO_SHORTCUT", raising=False)
    monkeypatch.delenv("SOUND_SYSTEM_DATA_DIR", raising=False)
    monkeypatch.delenv("SAFA_SHORTCUT_DIR", raising=False)
    return True


@pytest.fixture
def dev_root(tmp_path):
    """A dev-checkout-like root: start.bat + launcher ico, no Safa.exe."""
    root = tmp_path / "app"
    root.mkdir()
    (root / "start.bat").write_text("@echo off\n")
    return root


def _fake_run(calls: list, rc: int = 0):
    def run(argv, **kw):
        calls.append((argv, kw))
        return types.SimpleNamespace(returncode=rc)
    return run


def _argv(calls):
    return calls[0][0]


def _env(calls) -> dict:
    return calls[0][1]["env"]


def _command(calls) -> str:
    argv = _argv(calls)
    return argv[argv.index("-Command") + 1]


def _lnk_env(calls) -> dict:
    """The lnk fields, as env vars handed to the fixed script."""
    e = _env(calls)
    return {k: e[k] for k in ("SAFA_LNK_TARGET", "SAFA_LNK_WORKDIR",
                              "SAFA_LNK_ICON", "SAFA_LNK_DESC")}


# ---- skip rules --------------------------------------------------------------------


def test_skipped_on_non_windows(dev_root, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    calls = []
    out = shortcut.maybe_create_desktop_shortcut(
        dev_root, _run=_fake_run(calls))
    assert out == "skipped"
    assert calls == []


def test_env_opt_out_skips(dev_root, win32, monkeypatch):
    monkeypatch.setenv("SAFA_NO_SHORTCUT", "1")
    calls = []
    assert shortcut.maybe_create_desktop_shortcut(
        dev_root, _run=_fake_run(calls)) == "skipped"
    assert calls == []


def test_data_dir_env_skips(dev_root, win32, monkeypatch):
    """SOUND_SYSTEM_DATA_DIR marks Docker/tests — never shortcut there."""
    monkeypatch.setenv("SOUND_SYSTEM_DATA_DIR", str(dev_root / "d"))
    calls = []
    assert shortcut.maybe_create_desktop_shortcut(
        dev_root, _run=_fake_run(calls)) == "skipped"
    assert calls == []


def test_installed_mode_skips(dev_root, win32):
    """installed.json → the Inno installer owns the desktop icon."""
    (dev_root / "installed.json").write_text('{"data_dir": "x"}')
    calls = []
    assert shortcut.maybe_create_desktop_shortcut(
        dev_root, _run=_fake_run(calls)) == "skipped"
    assert calls == []


def test_portable_txt_beats_installed_json(dev_root, win32):
    """A portable tree extracted over an install stays portable — the
    shortcut logic mirrors paths.py mode resolution."""
    (dev_root / "installed.json").write_text('{"data_dir": "x"}')
    (dev_root / "portable.txt").write_text("portable\n")
    (dev_root / "Safa.exe").write_text("x")
    calls = []
    assert shortcut.maybe_create_desktop_shortcut(
        dev_root, _run=_fake_run(calls)) == "created"
    assert calls


def test_existing_marker_skips(dev_root, win32):
    data = dev_root / "data"
    data.mkdir()
    (data / ".desktop-shortcut").write_text("done\n")
    calls = []
    assert shortcut.maybe_create_desktop_shortcut(
        dev_root, _run=_fake_run(calls)) == "skipped"
    assert calls == []


# ---- outcomes -----------------------------------------------------------------------


def test_created_writes_marker_once(dev_root, win32):
    calls = []
    run = _fake_run(calls, rc=0)
    assert shortcut.maybe_create_desktop_shortcut(
        dev_root, _run=run) == "created"
    marker = dev_root / "data" / ".desktop-shortcut"
    assert marker.is_file()
    # second call: marker stops the attempt entirely
    assert shortcut.maybe_create_desktop_shortcut(
        dev_root, _run=run) == "skipped"
    assert len(calls) == 1


def test_exit_10_still_writes_marker(dev_root, win32):
    """Link already on the Desktop → marker anyway (a user who deletes
    the shortcut is never asked again)."""
    calls = []
    assert shortcut.maybe_create_desktop_shortcut(
        dev_root, _run=_fake_run(calls, rc=10)) == "exists"
    assert (dev_root / "data" / ".desktop-shortcut").is_file()


def test_failure_writes_no_marker(dev_root, win32, capsys):
    calls = []
    assert shortcut.maybe_create_desktop_shortcut(
        dev_root, _run=_fake_run(calls, rc=12)) == "failed"
    assert not (dev_root / "data" / ".desktop-shortcut").exists()
    # one non-fatal warning line, nothing more
    out = capsys.readouterr().out.strip()
    assert out.count("\n") == 0 and "Warning" in out


def test_runner_raising_writes_no_marker(dev_root, win32, capsys):
    def boom(argv, **kw):
        raise OSError("no powershell")
    assert shortcut.maybe_create_desktop_shortcut(
        dev_root, _run=boom) == "failed"
    assert not (dev_root / "data" / ".desktop-shortcut").exists()


# ---- process + payload contract -------------------------------------------------------


def test_portable_targets_exe_and_self_icon(dev_root, win32):
    (dev_root / "Safa.exe").write_text("x")
    calls = []
    assert shortcut.maybe_create_desktop_shortcut(
        dev_root, _run=_fake_run(calls)) == "created"
    f = _lnk_env(calls)
    assert f["SAFA_LNK_TARGET"] == str(dev_root / "Safa.exe")
    assert f["SAFA_LNK_ICON"] == str(dev_root / "Safa.exe")
    assert f["SAFA_LNK_WORKDIR"] == str(dev_root)
    assert f["SAFA_LNK_DESC"] == "Safa (صفا)"


def test_dev_targets_start_bat_launcher_ico(dev_root, win32):
    (dev_root / "packaging" / "windows" / "launcher").mkdir(parents=True)
    (dev_root / "packaging" / "windows" / "launcher" / "safa.ico"
     ).write_text("x")
    calls = []
    shortcut.maybe_create_desktop_shortcut(dev_root, _run=_fake_run(calls))
    f = _lnk_env(calls)
    assert f["SAFA_LNK_TARGET"] == str(dev_root / "start.bat")
    assert f["SAFA_LNK_ICON"] == str(
        dev_root / "packaging" / "windows" / "launcher" / "safa.ico")


def test_dev_falls_back_to_dist_favicon(dev_root, win32):
    (dev_root / "frontend" / "dist").mkdir(parents=True)
    (dev_root / "frontend" / "dist" / "favicon.ico").write_text("x")
    calls = []
    shortcut.maybe_create_desktop_shortcut(dev_root, _run=_fake_run(calls))
    assert _lnk_env(calls)["SAFA_LNK_ICON"] == str(
        dev_root / "frontend" / "dist" / "favicon.ico")


def test_invocation_is_fixed_and_hidden(dev_root, win32):
    calls = []
    shortcut.maybe_create_desktop_shortcut(dev_root, _run=_fake_run(calls))
    argv, kw = calls[0]
    assert argv[:5] == ["powershell", "-NoProfile", "-NonInteractive",
                        "-ExecutionPolicy", "Bypass"]
    assert kw["timeout"] <= 20
    if sys.platform == "win32":
        assert kw.get("creationflags") == subprocess.CREATE_NO_WINDOW
    # the Desktop is resolved inside PowerShell, not by us
    cmd = _command(calls)
    assert "GetFolderPath" in cmd
    # Unicode-safe IShellLinkW (not ANSI WScript.Shell) writes the link
    assert "IShellLinkW" in cmd and "SetDescription" in cmd
    assert "WScript.Shell" not in cmd


def test_paths_never_interpolated_into_script(tmp_path, win32):
    """Arabic + quote + $ in the root must travel via env only."""
    root = tmp_path / "Safa $it's تجربة"
    root.mkdir()
    (root / "start.bat").write_text("x")
    (root / "Safa.exe").write_text("x")
    calls = []
    shortcut.maybe_create_desktop_shortcut(root, _run=_fake_run(calls))
    cmd = _command(calls)
    for frag in (str(root), "تجربة", "$it", "it's"):
        assert frag not in cmd, frag
    env = _env(calls)
    assert env["SAFA_LNK_TARGET"] == str(root / "Safa.exe")
    assert env["SAFA_LNK_WORKDIR"] == str(root)
    assert "تجربة" in env["SAFA_LNK_TARGET"]


def test_shortcut_dir_override_reaches_script_env(dev_root, win32,
                                                  monkeypatch):
    """SAFA_SHORTCUT_DIR exists only for the manual temp-folder check."""
    d = dev_root / "elsewhere"
    monkeypatch.setenv("SAFA_SHORTCUT_DIR", str(d))
    calls = []
    shortcut.maybe_create_desktop_shortcut(dev_root, _run=_fake_run(calls))
    assert _env(calls)["SAFA_SHORTCUT_DIR"] == str(d)


# ---- launcher integration ---------------------------------------------------------------


def test_launcher_survives_shortcut_raise(monkeypatch):
    from backend import launcher
    monkeypatch.setattr(
        shortcut, "maybe_create_desktop_shortcut",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert launcher._first_run_shortcut() is None


def test_launcher_prints_bilingual_on_created(monkeypatch, capsys):
    from backend import launcher
    monkeypatch.setattr(
        shortcut, "maybe_create_desktop_shortcut", lambda *a, **k: "created")
    launcher._first_run_shortcut()
    out = capsys.readouterr().out
    assert "shortcut" in out.lower() or "اختصار" in out
