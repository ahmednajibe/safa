"""Inno installer contract tests (M10c §9, review fixes).

Static checks over packaging/windows/inno/*.iss — the behavioral proof
is the real silent install/uninstall run done during release builds:

- install writes a ``.safa-data`` marker into the chosen data folder;
- the uninstaller offers data deletion ONLY when that marker exists
  (a user-picked Documents folder or drive root is never DelTree'd);
- data_dir is read at ``usUninstall`` (before installed.json is gone);
- runtime-created ``installed.json`` is removed at uninstall so the
  app dir does not linger;
- the kept-folder path is shown to the user (bilingual).
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INNO = ROOT / "packaging" / "windows" / "inno"
COMMON = (INNO / "common.iss").read_text(encoding="utf-8")


def _uspostuninstall_segment() -> str:
    idx = COMMON.index("CurUninstallStep = usPostUninstall")
    return COMMON[idx:]


def test_install_writes_safa_data_marker():
    assert ".safa-data" in COMMON
    # the marker write happens at install time into SafaDataDir()
    idx = COMMON.index(".safa-data")
    assert "SaveStringToFile" in COMMON[max(0, idx - 400):idx + 200]


def test_uninstall_removes_postinstall_artifacts():
    """{app} must not linger: install-runtime.ps1 creates python/ and
    pyroot/, the launcher creates data/.unblocked — none are Inno-tracked
    so the uninstaller deletes them explicitly before RemoveDir."""
    seg = _uspostuninstall_segment()
    for sub in ("{app}\\python", "{app}\\pyroot", "{app}\\data",
                "{app}\\models"):
        assert f"SweepAppSubdir(ExpandConstant('{sub}'), UninstDataDir)" in seg, sub


def test_app_subdir_sweep_spares_overlapping_data_folder():
    """A data folder the user picked inside {app} (e.g. {app}\\data) must
    never be swept without the marker prompt: SweepAppSubdir skips any
    subfolder that overlaps a still-existing data folder."""
    idx = COMMON.index("procedure SweepAppSubdir")
    body = COMMON[idx:COMMON.index("end;", COMMON.index("exit;", idx)) + 200]
    assert "PathsOverlap(Sub, DataDir)" in body
    assert "DirExists(DataDir)" in body
    assert "exit;" in body
    seg = _uspostuninstall_segment()
    # no bare DelTree on {app} subfolders remains in the post-uninstall step
    assert "DelTree(ExpandConstant('{app}" not in seg
    assert "DeleteFile(ExpandConstant('{app}\\installed.json'))" in seg
    assert "RemoveDir(ExpandConstant('{app}'))" in seg


def test_uninstall_reads_data_dir_before_app_files_go():
    """installed.json dies with {app} — capture data_dir at usUninstall."""
    assert "UninstDataDir" in COMMON
    idx = COMMON.index("CurUninstallStep = usUninstall")
    # the usUninstall branch must read installed.json's data_dir
    assert "ReadInstalledDataDir" in COMMON[idx:idx + 300]


def test_uninstall_delete_offer_requires_marker():
    assert "usPostUninstall" in COMMON
    post = COMMON[COMMON.index("CurUninstallStep = usPostUninstall"):]
    assert "FileExists" in post and ".safa-data" in post
    assert "DeleteOwnedDataEntries(D)" in post


def test_uninstall_shows_kept_path_and_removes_installed_json():
    post = COMMON[COMMON.index("CurUninstallStep = usPostUninstall"):]
    assert "DataKept" in post  # bilingual kept-message, path shown
    assert "DeleteFile" in post and "installed.json" in post


def test_kept_message_is_bilingual():
    for lang in ("en.", "ar."):
        assert f"{lang}DataKeptPrompt=" in COMMON.replace(" ", "") or \
            f"{lang}DataKept" in COMMON


def test_desktop_icon_checked_by_default_and_uses_exe_icon():
    """R3 §4: the desktop-icon task is opt-out (no ``unchecked`` flag)
    and the {autodesktop} entry renders with the Safa.exe icon."""
    for iss in ("online.iss", "offline-cpu.iss", "offline-gpu.iss"):
        text = (INNO / iss).read_text(encoding="utf-8")
        task = [ln for ln in text.splitlines()
                if '"desktopicon"' in ln]
        assert task, iss
        assert all("unchecked" not in ln for ln in task), iss
        desk = [ln for ln in text.splitlines() if "{autodesktop}" in ln]
        assert desk, iss
        assert all('IconFilename: "{app}\\Safa.exe"' in ln
                   for ln in desk), iss


def test_each_variant_calls_write_installed_json():
    for iss in ("online.iss", "offline-cpu.iss", "offline-gpu.iss"):
        text = (INNO / iss).read_text(encoding="utf-8")
        assert "WriteInstalledJson" in text, iss


def test_gpu_slice_guard_present():
    text = (INNO / "offline-gpu.iss").read_text(encoding="utf-8")
    assert "slicecount.txt" in text
    assert "InitializeSetup" in text


def test_gpu_slice_guard_works_without_slicecount():
    """v0.1.0: a browser user downloads only the exe — no slicecount.txt —
    and the guard silently skipped, so setup started and then showed Inno's
    'insert disk' prompt. Slice 1 must be required regardless, and the
    message must name the real file names."""
    text = (INNO / "offline-gpu.iss").read_text(encoding="utf-8")
    body = text[text.index("function InitializeSetup"):]
    body = body[:body.index("\nend;")]
    assert "N := 1;" in body
    assert "exit;" not in body.split("Missing := '';")[0], (
        "guard must not bail out when slicecount.txt is absent")
    assert "win-x64-gpu-offline-setup-%d.bin" in text
    assert "SliceName(I)" in body


# ---- R2 #9: pre-existing data folders must survive install/uninstall --------------


def test_marker_skipped_for_preexisting_nonempty_data_dir():
    """``.safa-data`` is written only when the data folder was absent or
    empty BEFORE install — a pre-existing Documents folder with user
    files must never be marked as Safa-owned."""
    idx = COMMON.index("procedure WriteInstalledJson")
    body = COMMON[idx:idx + 3500]
    # the empty/absent capture runs BEFORE ForceDirectories materialises it
    assert "DirIsEmpty" in body
    assert body.index("DirIsEmpty") < body.index("ForceDirectories")
    # the marker write is conditional on that capture
    midx = body.index(".safa-data")
    assert "if " in body[:midx]


def test_uninstall_deletes_only_safa_owned_entries():
    """Uninstall 'Yes' deletes only entries the app itself creates
    (paths.py/storage/settings enumeration), then removes the folder
    iff nothing foreign remains — never a bare DelTree of the root."""
    post = _uspostuninstall_segment()
    assert "DelTree(D, True, True, True)" not in post
    assert "DeleteOwnedDataEntries(D)" in post
    assert "RemoveDir(D)" in post
    idx = COMMON.index("procedure DeleteOwnedDataEntries")
    body = COMMON[idx:idx + 2500]
    for owned in ("app.db", "jobs", "media", "logs", "models", "tmp",
                  ".safa-data"):
        assert owned in body, owned


def test_dir_is_empty_skips_dot_entries():
    """Inno's ``FindFirst`` matches '.' / '..' — ``DirIsEmpty`` must skip
    them or an existing-but-empty data folder never gets the marker
    (verified live: silent install logged 'not empty' on an empty dir)."""
    idx = COMMON.index("function DirIsEmpty")
    body = COMMON[idx:idx + 1800]
    assert "'.'" in body and "'..'" in body
    assert "FindNext" in body   # iterates past the dot entries


# ---- R2 #11: interactive installs must honour the wizard's variant page ----------


def test_online_variant_wizard_choice_honored():
    """``SelectedVariant()`` must read ``VariantPage.Values`` in
    interactive mode — ``/VARIANT`` / auto-detect may only decide when
    the param was given explicitly or the install is silent."""
    text = (INNO / "online.iss").read_text(encoding="utf-8")
    idx = text.index("function SelectedVariant")
    body = text[idx:idx + 1800]
    assert "VariantPage.Values" in body
    assert "WizardSilent" in body


def test_online_variant_page_defaults_to_detection():
    """The wizard's preselect follows GPU detection — a CPU-only machine
    must not default to cu124 (the runtime install would fail on it)."""
    text = (INNO / "online.iss").read_text(encoding="utf-8")
    idx = text.index("procedure InitializeWizard")
    body = text[idx:idx + 2000]
    assert "GpuDetected" in body[body.index("VariantPage.Values"):]


# ---- v0.1.1: live runtime progress in the online installer ----------------------


def test_online_runtime_step_does_not_block_the_wizard():
    """v0.1.0 ran install-runtime.ps1 with ewWaitUntilTerminated behind a
    static page for 20-40 min (looked frozen). It must run ewNoWait and be
    polled through the status file, with a working Cancel."""
    text = (INNO / "online.iss").read_text(encoding="utf-8")
    body = text[text.index("function RunRuntimeInstall"):]
    body = body[:body.index("procedure CurStepChanged")]
    assert "ewNoWait" in body
    assert "ewWaitUntilTerminated, ResultCode);\n    if ResultCode" not in body
    assert "-StatusFile" in body
    assert "SetProgress(Overall, 1000)" in body
    assert "taskkill" in body and "/T /F /PID" in body
    assert "CancelButton.OnClick := @RuntimeCancelClick" in body
    # Inno treats ANY line starting with '[' as a section header
    for n, line in enumerate(text.splitlines(), 1):
        if line.startswith("[") and not line.rstrip().endswith("]"):
            raise AssertionError(f"online.iss:{n} starts with '[': {line!r}")


def test_install_runtime_status_protocol_and_cache_cleanup():
    s = (ROOT / "scripts" / "install-runtime.ps1").read_text(encoding="utf-8-sig")
    assert "[string]$StatusFile" in s
    # <pid>|<step>|<steps>|<done_mb>|<total_mb>|<eta_s>|<state>|<exit>
    assert '"$PID|$Step|$Steps|$DoneMB|$TotalMB|$EtaS|$State|$Code"' in s
    assert '"done"' in s and '"fail"' in s
    # the multi-GB download cache is deleted after a good install
    assert "Remove-Item -LiteralPath $env:UV_CACHE_DIR" in s
    assert "if (-not $KeepCache)" in s
