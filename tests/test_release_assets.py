"""M10c review fix: release.yml uploads ONLY release-info.toml assets.

``scripts/list-release-assets.py`` globs a dist_pkg tree against the
[assets] patterns (any depth — GPU portable pieces live in
dist_pkg/gpu-portable/) and optionally copies matches flat into a
staging dir. Staging folders, extracted portable trees, logs and stray
files must never match.

Also static release.yml / ci.yml contract checks:
- no ``secrets.`` in ``if:`` (workflows fail to load otherwise);
- uploads target dist_release/, never dist_pkg/;
- SHA256SUMS.txt is computed in the publish job over merged assets;
- the CI install log lives under data/tmp.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "list-release-assets.py"
REL = (ROOT / ".github" / "workflows" / "release.yml").read_text(
    encoding="utf-8")
CI = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

ASSET_FILES = [
    "Safa-0.1.0-win-x64-setup.exe",
    "Safa-0.1.0-win-x64-cpu-offline-setup.exe",
    "Safa-0.1.0-win-x64-gpu-offline-setup.exe",
    "Safa-0.1.0-win-x64-gpu-offline-setup-1.bin",
    "Safa-0.1.0-win-x64-gpu-offline-setup-2.bin",
    "Safa-0.1.0-win-x64-cpu-portable.zip",
    "Safa-0.1.0-source.zip",
    "Safa-models-core-0.1.0.zip",
    "Safa-models-all-0.1.0.zip",
    "slicecount.txt",
    "gpu-portable/Safa-0.1.0-win-x64-gpu-portable.7z.001",
    "gpu-portable/Safa-0.1.0-win-x64-gpu-portable.7z.002",
    "gpu-portable/Safa-0.1.0-win-x64-gpu-portable-SHA256SUMS.txt",
    "gpu-portable/assemble-gpu.bat",
    "gpu-portable/7z.exe",
    "gpu-portable/7z.dll",
    # M10d wave 2
    "Safa-0.1.0-macos-arm64.tar.gz",
    "Safa-0.1.0-macos-arm64.dmg",
    "Safa-0.1.0-linux-x64-cpu.tar.gz",
    "safa_audio-0.1.0-py3-none-any.whl",
    "safa_audio-0.1.0.tar.gz",
    "Safa-0.1.0-linux-x64-cpu.deb",
    "rpm/Safa-0.1.0-linux-x64-cpu.rpm",
    "Safa-0.1.0-linux-x64.AppImage",
    "flatpak/Safa-0.1.0-linux-x64.flatpak",
    "flatpak/org.safa.Safa.yaml",
]

NON_ASSET_FILES = [
    "ci-inst.log",
    "Safa-0.1.0-win-x64-cpu-portable/python/python.exe",
    "Safa-0.1.0-win-x64-gpu-portable/backend/main.py",
    "gpu-portable/stage/leftover.bin",
    "SoundSystem-0.1.0-win64-cpu.zip",          # stale pre-rebrand name
    "Safa-0.1.0-win-x64-setup.exe.tmp",          # partial download
    # R2 #3: CUDA linux payloads can never publish (2 GiB cap) — not
    # release assets anymore; cuda installs provision cu124 online.
    "Safa-0.1.0-linux-x64-cuda.tar.gz",
    "Safa-0.1.0-linux-x64-cuda.deb",
    "rpm/Safa-0.1.0-linux-x64-cuda.rpm",
    "notes.txt",
]


def _fake_dist(tmp_path: Path) -> Path:
    dist = tmp_path / "dist_pkg"
    for rel in ASSET_FILES + NON_ASSET_FILES:
        p = dist / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    return dist


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, timeout=60, cwd=ROOT,
    )


def test_list_release_assets_matches_only_assets(tmp_path):
    dist = _fake_dist(tmp_path)
    r = _run(str(dist))
    assert r.returncode == 0, r.stderr
    got = set(r.stdout.strip().splitlines())
    expected = {Path(f).name for f in ASSET_FILES}
    assert got == expected
    for rel in NON_ASSET_FILES:
        assert Path(rel).name not in got, rel


def test_list_release_assets_dest_copies_flat(tmp_path):
    dist = _fake_dist(tmp_path)
    dest = tmp_path / "dist_release"
    r = _run(str(dist), "--dest", str(dest))
    assert r.returncode == 0, r.stderr
    copied = {p.name for p in dest.iterdir() if p.is_file()}
    assert copied == {Path(f).name for f in ASSET_FILES}
    # nested source files land flat
    assert (dest / "Safa-0.1.0-win-x64-gpu-portable.7z.001").is_file()


def test_list_release_assets_version_override(tmp_path):
    dist = tmp_path / "dist_pkg"
    (dist / "x").mkdir(parents=True)
    (dist / "Safa-9.9.9-win-x64-setup.exe").write_bytes(b"x")
    (dist / "Safa-0.1.0-win-x64-setup.exe").write_bytes(b"x")
    r = _run(str(dist), "--version", "9.9.9")
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "Safa-9.9.9-win-x64-setup.exe"


def test_list_release_assets_empty_tree_errors(tmp_path):
    dist = tmp_path / "dist_pkg"
    dist.mkdir()
    r = _run(str(dist))
    assert r.returncode != 0


# ---- workflow contract ------------------------------------------------------------


def test_release_no_secrets_in_if_conditions():
    """secrets cannot appear in `if:` — map to job env instead."""
    for i, line in enumerate(REL.splitlines()):
        if "if:" in line and "${{" in line:
            assert "secrets." not in line, f"release.yml line {i + 1}: {line}"
        if "if:" in line and "secrets." in line:
            raise AssertionError(
                f"release.yml line {i + 1}: secrets in if: {line}")


def test_release_uploads_only_staged_assets():
    assert "path: dist_pkg" not in REL
    assert "dist_release/" in REL
    assert "list-release-assets.py" in REL


def test_release_sums_computed_in_publish_job():
    publish = REL[REL.index("  publish:"):]
    assert "sha256sum" in publish
    assert "SHA256SUMS.txt" in publish
    # the windows job must NOT compute the sums file itself
    windows = REL[:REL.index("  macos:")]
    assert "SHA256SUMS" not in windows


def test_ci_install_log_under_data_tmp():
    assert "dist_pkg\\ci-inst.log" not in REL
    assert "dist_pkg/ci-inst.log" not in REL
    assert "data\\tmp\\ci-inst.log" in REL or "data/tmp/ci-inst.log" in REL


def test_workflow_files_parse_as_yaml():
    import yaml  # type: ignore  (test dep only — PyYAML is a dev dep)
    for doc in (REL, CI):
        yaml.safe_load(doc)

def test_release_builds_no_linux_cuda_assets():
    """R2 #3: CUDA linux payloads (~4.9 GB) can never publish — release.yml
    stops building them; install.sh provisions cu124 online instead."""
    assert "package-unix.sh --variant cuda" not in REL
    assert "deb --variant cuda" not in REL
    assert "rpm --variant cuda" not in REL


def test_publish_drops_oversize_assets_before_sums_and_upload():
    """One asset >= 2 GiB fails the whole GitHub upload: the publish job
    must drop them before hashing and releasing."""
    import yaml  # type: ignore

    wf = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "release.yml").read_text("utf-8"))
    steps = wf["jobs"]["publish"]["steps"]
    names = [s.get("name", "") for s in steps]
    drop = next(i for i, n in enumerate(names) if "2 GiB" in n)
    sums = next(i for i, n in enumerate(names) if "SHA256SUMS" in n)
    rel = next(i for i, s in enumerate(steps)
               if "gh release create" in s.get("run", ""))
    assert drop < sums < rel
    assert "-size +2047M" in steps[drop]["run"]
