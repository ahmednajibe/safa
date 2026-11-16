"""Linux packages (.deb / .rpm / AppImage) — static contract tests (M10d).

This machine has no dpkg-deb / rpmbuild / appimagetool and no WSL, so
the packages themselves are built in CI; here we verify the contract:
layout, wrapper semantics, data preservation, and script dry-run.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "scripts" / "package-linux.sh"
LINUX = ROOT / "packaging" / "linux"
APPIMAGE = ROOT / "packaging" / "appimage"
SPEC = (ROOT / "packaging" / "rpm" / "safa.spec").read_text("utf-8")
SCRIPT = PKG.read_text("utf-8")
WRAPPER = (LINUX / "safa").read_text("utf-8")
CONTROL = (LINUX / "control.template").read_text("utf-8")
POSTRM = (LINUX / "postrm").read_text("utf-8")
DESKTOP = (LINUX / "safa.desktop").read_text("utf-8")
APPRUN = (APPIMAGE / "AppRun").read_text("utf-8")


def _sh(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["sh", str(PKG), *args], capture_output=True, text=True,
        timeout=30, cwd=ROOT)


# ---- script ----------------------------------------------------------------


def test_package_linux_usage():
    r = _sh()
    assert r.returncode == 2
    assert "deb" in r.stderr and "rpm" in r.stderr and "appimage" in r.stderr


def test_package_linux_dry_run_deb():
    r = _sh("deb", "--dry-run")
    assert r.returncode == 0, r.stderr
    assert "dpkg-deb" in r.stdout


def test_package_linux_appimage_refuses_cuda():
    # the CUDA payload (~4.9 GB) exceeds the 2 GiB asset limit — the
    # AppImage is CPU-only and must fail loudly if cuda is requested.
    r = _sh("appimage", "--variant", "cuda", "--dry-run")
    assert r.returncode == 2
    assert "2 GiB" in r.stdout


def test_package_linux_dry_run_appimage():
    r = _sh("appimage", "--dry-run")
    assert r.returncode == 0, r.stderr
    assert "Safa.AppDir" in r.stdout or "AppDir" in r.stdout


def test_no_lf_issues():
    for p in (PKG, LINUX / "safa", LINUX / "postrm", LINUX / "postinst",
              APPIMAGE / "AppRun"):
        assert b"\r\n" not in p.read_bytes(), p


# ---- deb layout ------------------------------------------------------------


def test_deb_layout_paths():
    for piece in ("/opt/safa", "/usr/bin/safa",
                  "usr/share/applications", "hicolor/256x256/apps/safa.png",
                  "--root-owner-group"):
        assert piece in SCRIPT, piece


def test_deb_wrapper_sets_xdg_data_dir():
    # /opt/safa is root-owned → the wrapper MUST export the env dir
    assert 'SOUND_SYSTEM_DATA_DIR="$XDG_DATA_HOME/safa-data"' in WRAPPER
    assert "${XDG_DATA_HOME:=$HOME/.local/share}" in WRAPPER
    assert "backend.launcher" in WRAPPER
    # loopback stays the launcher's default; wrapper adds no host flag
    assert "host" not in WRAPPER.lower() or "--host" not in WRAPPER


def test_deb_removes_portable_marker():
    # portable.txt would switch paths.py back to app-dir data — wrong
    # for a root-owned install; it must be stripped from the payload.
    assert "rm -f" in SCRIPT and "portable.txt" in SCRIPT
    assert 'rm -f %{buildroot}/opt/safa/portable.txt' in SPEC


def test_deb_control_contract():
    for field in ("Package: {{PKG}}", "Version: {{VERSION}}",
                  "Architecture: amd64", "Conflicts: {{CONFLICT}}",
                  "Installed-Size"):
        assert field in CONTROL, field
    # bundled ffmpeg choice is documented in the control description
    assert "ffmpeg is bundled" in CONTROL or "bundled" in CONTROL


def test_deb_postrm_never_deletes_data():
    assert "purge" in POSTRM          # even purge keeps user data
    assert "user data" in POSTRM
    code = "\n".join(l for l in POSTRM.splitlines()
                     if l.strip() and not l.lstrip().startswith("#"))
    for bad in ("rm ", "rmdir", "safa-data"):
        assert bad not in code, bad


def test_deb_requires_tarball_first():
    # the deb is rearranged from package-unix's stage — never rebuilds
    assert "package-unix.sh" in SCRIPT
    assert "tar -xzf" in SCRIPT


# ---- rpm -------------------------------------------------------------------


def test_rpm_spec_layout_and_wrapper():
    for piece in ("/opt/safa", "%{_bindir}/safa", "%{_datadir}/applications",
                  "icons/hicolor/256x256/apps/safa.png"):
        assert piece in SPEC, piece
    assert "%{safaver}" in SPEC and "%{safavariant}" in SPEC
    assert "%{safapkg}" in SPEC


def test_rpm_spec_no_data_deletion():
    postun = SPEC[SPEC.rindex("%postun"):]   # the real section, not the comment
    assert "NEVER delete" in postun or "survives" in postun
    code = re.sub(r"#.*", "", postun)
    assert "rm -rf" not in code and "safa-data" not in code


def test_rpm_spec_bundled_ffmpeg_documented():
    assert "bundled" in SPEC.lower()
    # no system ffmpeg requirement (Fedora base lacks it)
    assert "Requires:" not in SPEC


def test_rpm_cpu_cuda_conflict():
    assert "Conflicts:" in SPEC


# ---- appimage ---------------------------------------------------------------


def test_appimage_layout():
    for piece in ("Safa.AppDir", "lib/safa", "AppRun", "safa.desktop",
                  "safa.png"):
        assert piece in SCRIPT, piece


def test_apprun_sets_xdg_data_dir():
    assert "SOUND_SYSTEM_DATA_DIR" in APPRUN
    assert "safa-data" in APPRUN
    assert "XDG_DATA_HOME" in APPRUN
    assert "backend.launcher" in APPRUN


def test_appimage_desktop_and_icon_at_root():
    # appimagetool convention: <name>.desktop + <name>.png at AppDir root
    assert '"$ADIR/safa.desktop"' in SCRIPT
    assert '"$ADIR/safa.png"' in SCRIPT


# ---- security contract ------------------------------------------------------


def test_no_firewall_or_non_loopback():
    for src in (SCRIPT, WRAPPER, APPRUN, SPEC):
        assert "0.0.0.0" not in src
        assert "firewall" not in src.lower() or "no firewall" in src.lower()


def test_version_parsed_from_pyproject():
    assert 'pyproject.toml' in SCRIPT
    assert "release-info.toml" in SCRIPT  # repo URL also sourced
