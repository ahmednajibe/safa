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
    assert "DelTree" in post


def test_uninstall_shows_kept_path_and_removes_installed_json():
    post = COMMON[COMMON.index("CurUninstallStep = usPostUninstall"):]
    assert "DataKept" in post  # bilingual kept-message, path shown
    assert "DeleteFile" in post and "installed.json" in post


def test_kept_message_is_bilingual():
    for lang in ("en.", "ar."):
        assert f"{lang}DataKeptPrompt=" in COMMON.replace(" ", "") or \
            f"{lang}DataKept" in COMMON


def test_each_variant_calls_write_installed_json():
    for iss in ("online.iss", "offline-cpu.iss", "offline-gpu.iss"):
        text = (INNO / iss).read_text(encoding="utf-8")
        assert "WriteInstalledJson" in text, iss


def test_gpu_slice_guard_present():
    text = (INNO / "offline-gpu.iss").read_text(encoding="utf-8")
    assert "slicecount.txt" in text
    assert "InitializeSetup" in text
