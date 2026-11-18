"""scripts/bump_manifests.py — the committed package-manager manifests
(winget, Scoop, Homebrew, AUR) get the real version + sha256 sums from a
published SHA256SUMS.txt.

Every test runs ``main(root=tmp_path)`` against a tmp tree holding copies
of the real manifests plus a minimal release-info.toml/pyproject — the
committed files are never touched.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import bump_manifests  # noqa: E402

VER = "9.9.9"
DATE = "2026-11-18"
REPO = "https://example.test/safa"

HASH = {
    "online_setup": "aa" * 32,
    "cpu_portable_zip": "bb" * 32,
    "macos_tarball": "cc" * 32,
    "linux_cpu_tarball": "dd" * 32,
}
ASSETS = {
    "online_setup": "Safa-{ver}-win-x64-setup.exe",
    "cpu_portable_zip": "Safa-{ver}-win-x64-cpu-portable.zip",
    "macos_tarball": "Safa-{ver}-macos-arm64.tar.gz",
    "linux_cpu_tarball": "Safa-{ver}-linux-x64-cpu.tar.gz",
    "sha256sums": "SHA256SUMS.txt",
}
MANIFESTS = (
    "packaging/winget/Safa.Safa.yaml",
    "packaging/winget/Safa.Safa.installer.yaml",
    "packaging/winget/Safa.Safa.locale.en-US.yaml",
    "packaging/winget/Safa.Safa.locale.ar-SA.yaml",
    "packaging/scoop/safa.json",
    "packaging/homebrew/safa.rb",
    "packaging/aur/PKGBUILD",
)


def _name(key: str) -> str:
    return ASSETS[key].format(ver=VER)


@pytest.fixture
def repo(tmp_path):
    """tmp_path becomes a mini repo root: real manifests + minimal toml."""
    (tmp_path / "pyproject.toml").write_text(
        f'[project]\nversion = "{VER}"\n', encoding="utf-8")
    assets = "\n".join(f'{k} = "{v}"' for k, v in ASSETS.items())
    (tmp_path / "release-info.toml").write_text(
        f'[app]\nrepo_url = "{REPO}"\n\n[assets]\n{assets}\n',
        encoding="utf-8")
    for rel in MANIFESTS:
        dst = tmp_path / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, dst)
    return tmp_path


def _sums(repo: Path, drop: str | None = None,
          prefixes: dict[str, str] | None = None,
          bad: str | None = None) -> Path:
    """SHA256SUMS.txt over the four needed assets (plus a distractor)."""
    prefixes = prefixes or {}
    lines = []
    for key in ASSETS:
        if key == "sha256sums" or key == drop:
            continue
        h = "nothex" if key == bad else HASH[key]
        lines.append(f"{h}  {prefixes.get(key, '')}{_name(key)}")
    lines.append(f"{'ee' * 32}  unrelated-asset.bin")
    p = repo / "SHA256SUMS.txt"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def _run(repo: Path, *extra: str) -> int:
    return bump_manifests.main(
        ["--sums", str(repo / "SHA256SUMS.txt"), *extra], root=repo)


def _snap(repo: Path) -> dict[str, bytes]:
    return {str(p.relative_to(repo)): p.read_bytes()
            for p in (repo / "packaging").rglob("*") if p.is_file()}


# ---- happy path ---------------------------------------------------------------


def test_every_field_rewritten(repo):
    _sums(repo)
    assert _run(repo, "--date", DATE) == 0

    for f in (repo / "packaging" / "winget").glob("*.yaml"):
        assert f'PackageVersion: "{VER}"' in f.read_text("utf-8"), f.name
    inst = (repo / "packaging/winget/Safa.Safa.installer.yaml"
            ).read_text("utf-8")
    assert (f'InstallerUrl: {REPO}/releases/download/v{VER}/'
            f'{_name("online_setup")}') in inst
    # winget convention: uppercase hex
    assert f'InstallerSha256: "{HASH["online_setup"].upper()}"' in inst
    assert f'ReleaseDate: "{DATE}"' in inst

    scoop = json.loads(
        (repo / "packaging/scoop/safa.json").read_text("utf-8"))
    assert scoop["version"] == VER
    a64 = scoop["architecture"]["64bit"]
    assert a64["url"] == (f"{REPO}/releases/download/v{VER}/"
                          f'{_name("cpu_portable_zip")}')
    assert a64["hash"] == HASH["cpu_portable_zip"]  # lowercase

    rb = (repo / "packaging/homebrew/safa.rb").read_text("utf-8")
    assert (f'  url "{REPO}/releases/download/v{VER}/'
            f'{_name("macos_tarball")}"') in rb
    assert f'  sha256 "{HASH["macos_tarball"]}"' in rb  # lowercase

    pkg = (repo / "packaging/aur/PKGBUILD").read_text("utf-8")
    assert f"pkgver={VER}" in pkg
    assert "pkgrel=1" in pkg
    assert f"sha256sums=('{HASH['linux_cpu_tarball']}')" in pkg  # lowercase

    # no placeholder survives anywhere, comments point at the script
    for p in (repo / "packaging").rglob("*"):
        if p.is_file():
            assert "<sha256" not in p.read_text("utf-8"), p.name
    assert "bump_manifests.py --sums SHA256SUMS.txt" in inst
    assert "bump_manifests.py --sums SHA256SUMS.txt" in rb
    assert "bump_manifests.py --sums SHA256SUMS.txt" in pkg


def test_scoop_autoupdate_hash_url(repo):
    _sums(repo)
    assert _run(repo) == 0
    scoop = json.loads(
        (repo / "packaging/scoop/safa.json").read_text("utf-8"))
    au = scoop["autoupdate"]["architecture"]["64bit"]
    assert au["url"].endswith("Safa-$version-win-x64-cpu-portable.zip")
    # Scoop resolves $version and reads the file's hash from the sums file
    assert au["hash"] == {
        "url": f"{REPO}/releases/download/v$version/SHA256SUMS.txt"}


def test_homebrew_has_no_version_field(repo):
    _sums(repo)
    assert _run(repo) == 0
    rb = (repo / "packaging/homebrew/safa.rb").read_text("utf-8")
    # Homebrew infers the version from the url — a version stanza must
    # never appear.
    assert not re.search(r"(?m)^\s*version\b", rb)
    assert f"v{VER}/" in rb and f"Safa-{VER}-" in rb


def test_idempotent_second_run(repo):
    _sums(repo)
    assert _run(repo, "--date", DATE) == 0
    before = _snap(repo)
    assert _run(repo, "--date", DATE) == 0
    assert _snap(repo) == before


def test_version_and_date_overrides(repo):
    _sums(repo)
    assert _run(repo, "--version", "9.9.9", "--date", DATE) == 0
    inst = (repo / "packaging/winget/Safa.Safa.installer.yaml"
            ).read_text("utf-8")
    assert f'ReleaseDate: "{DATE}"' in inst


# ---- sums file shapes ---------------------------------------------------------


def test_sums_star_and_dot_prefixes(repo):
    """sha256sum --binary writes '*name'; some generators write './name'."""
    _sums(repo, prefixes={"online_setup": "*",
                          "cpu_portable_zip": "./",
                          "macos_tarball": "*"})
    assert _run(repo, "--date", DATE) == 0
    scoop = json.loads(
        (repo / "packaging/scoop/safa.json").read_text("utf-8"))
    assert scoop["architecture"]["64bit"]["hash"] == HASH["cpu_portable_zip"]
    inst = (repo / "packaging/winget/Safa.Safa.installer.yaml"
            ).read_text("utf-8")
    assert f'"{HASH["online_setup"].upper()}"' in inst
    rb = (repo / "packaging/homebrew/safa.rb").read_text("utf-8")
    assert f'"{HASH["macos_tarball"]}"' in rb


# ---- refusal paths ------------------------------------------------------------


def test_missing_asset_refuses_and_writes_nothing(repo, capsys):
    _sums(repo, drop="macos_tarball")
    before = _snap(repo)
    assert _run(repo, "--date", DATE) == 2
    err = capsys.readouterr().err
    assert _name("macos_tarball") in err
    assert _snap(repo) == before


def test_bad_hash_refuses_and_writes_nothing(repo):
    _sums(repo, bad="cpu_portable_zip")
    before = _snap(repo)
    assert _run(repo, "--date", DATE) == 2
    assert _snap(repo) == before


def test_missing_sums_file_refuses(repo):
    assert bump_manifests.main(
        ["--sums", str(repo / "nope.txt")], root=repo) == 2


def test_bad_date_refuses(repo):
    _sums(repo)
    before = _snap(repo)
    assert _run(repo, "--date", "18/11/2026") == 2
    assert _snap(repo) == before
