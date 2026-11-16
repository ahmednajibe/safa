"""Chocolatey package (M10d wave 2).

- ``render_choco.py`` fills nuspec + tools scripts from release-info
  (version, repo, setup asset name, sha256, Inno AppId GUID).
- ``chocolateyinstall.ps1`` is dot-sourced with a mocked
  ``Install-ChocolateyPackage`` to check the exact arguments it passes
  (URL, sha256 checksum, silent args, /VARIANT= /DATADIR= mapping).
- ``chocolateyuninstall.ps1`` runs the Inno uninstaller silently and
  never deletes user data.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHOCO = ROOT / "packaging" / "chocolatey"
sys.path.insert(0, str(CHOCO))
import render_choco  # noqa: E402

PS = shutil.which("powershell") or shutil.which("pwsh")
INSTALL_PS1 = (CHOCO / "tools" / "chocolateyinstall.ps1").read_text(
    encoding="utf-8-sig")
UNINSTALL_PS1 = (CHOCO / "tools" / "chocolateyuninstall.ps1").read_text(
    encoding="utf-8-sig")


# ---- template + render -----------------------------------------------------


def test_templates_keep_tokens():
    nuspec = (CHOCO / "safa.nuspec").read_text(encoding="utf-8")
    for token in ("{{VERSION}}", "{{REPO}}"):
        assert token in nuspec
    for token in ("{{VERSION}}", "{{REPO}}", "{{SETUP_ASSET}}",
                  "{{SHA256}}"):
        assert token in INSTALL_PS1
    assert "{{APP_GUID}}" in UNINSTALL_PS1


def test_render_fills_release_info(tmp_path):
    written = render_choco.render("9.9.9", "ab" * 32, tmp_path)
    assert len(written) == 3
    nuspec = (tmp_path / "safa.nuspec").read_text("utf-8-sig")
    assert "<version>9.9.9</version>" in nuspec
    install = (tmp_path / "tools" / "chocolateyinstall.ps1").read_text(
        "utf-8-sig")
    assert "Safa-9.9.9-win-x64-setup.exe" in install
    assert ("releases/download/v9.9.9/Safa-9.9.9-win-x64-setup.exe"
            in install)
    assert "ab" * 32 in install
    uninstall = (tmp_path / "tools" / "chocolateyuninstall.ps1").read_text(
        "utf-8-sig")
    assert "{B6C2A7E1-4F3D-4E5A-9C8B-1A2B3C4D5E6F}" in uninstall
    for token in render_choco.TOKENS:
        assert token not in install + uninstall + nuspec, token


def test_render_sums_lookup(tmp_path):
    sums = tmp_path / "SHA256SUMS.txt"
    sums.write_text(
        f"{'cd'*32}  Safa-9.9.9-win-x64-setup.exe\n"
        f"{'ef'*32}  other.bin\n", encoding="utf-8")
    assert render_choco.sum_from_sums_file(
        sums, "Safa-9.9.9-win-x64-setup.exe") == "cd" * 32


def test_nuspec_parses_as_xml():
    import xml.etree.ElementTree as ET
    rendered = (CHOCO / "safa.nuspec").read_text(encoding="utf-8")
    for t, v in (("{{VERSION}}", "0.1.0"), ("{{REPO}}", "https://x"),
                 ("{{SETUP_ASSET}}", "s.exe"), ("{{SHA256}}", "0" * 64)):
        rendered = rendered.replace(t, v)
    ET.fromstring(rendered)


# ---- install script: dot-source with mocked Install-ChocolateyPackage -------


def _ps_dot_source(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [PS, "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-Command", script],
        capture_output=True, text=True, timeout=120, encoding="utf-8",
        errors="replace")


@pytest.mark.skipif(PS is None, reason="powershell not on PATH")
def test_choco_install_args_cpu(tmp_path):
    """Dot-source the rendered install.ps1 with a mock — capture the
    packageArgs hashtable Install-ChocolateyPackage receives."""
    rendered = tmp_path / "rendered"
    render_choco.render("0.1.0", "ab" * 32, rendered)
    ps1 = rendered / "tools" / "chocolateyinstall.ps1"
    harness = f"""
$env:LOCALAPPDATA = '{tmp_path}'
$env:ChocolateyPackageParameters = ''
$global:cap = $null
function global:Install-ChocolateyPackage {{
    param($packageName,$fileType,$url,$checksum,$checksumType,$silentArgs,$validExitCodes)
    $global:cap = $PSBoundParameters
}}
. '{ps1}'
$global:cap | ConvertTo-Json -Compress
"""
    r = _ps_dot_source(harness)
    assert r.returncode == 0, r.stderr
    cap = json.loads(r.stdout.strip().splitlines()[-1])
    assert cap["fileType"] == "exe"
    assert cap["checksum"] == "ab" * 32
    assert cap["checksumType"] == "sha256"
    assert cap["url"].endswith("Safa-0.1.0-win-x64-setup.exe")
    for token in ("/VERYSILENT", "/NORESTART", "/CURRENTUSER",
                  "/VARIANT=auto"):
        assert token in cap["silentArgs"]


@pytest.mark.skipif(PS is None, reason="powershell not on PATH")
def test_choco_install_params_mapping(tmp_path):
    """--params maps to Inno args."""
    rendered = tmp_path / "rendered"
    render_choco.render("0.1.0", "ab" * 32, rendered)
    ps1 = rendered / "tools" / "chocolateyinstall.ps1"
    harness = f"""
$env:LOCALAPPDATA = '{tmp_path}'
$env:ChocolateyPackageParameters = '/Variant:gpu /DataDir:"D:\\Safa Data" /InstallDir:D:\\Apps\\Safa'
$global:cap = $null
function global:Install-ChocolateyPackage {{
    param($packageName,$fileType,$url,$checksum,$checksumType,$silentArgs,$validExitCodes)
    $global:cap = $PSBoundParameters
}}
. '{ps1}'
$global:cap.silentArgs
"""
    r = _ps_dot_source(harness)
    assert r.returncode == 0, r.stderr
    args = r.stdout.strip()
    assert "/VARIANT=gpu" in args
    assert '/DATADIR="D:\\Safa Data"' in args
    assert '/DIR="D:\\Apps\\Safa"' in args


@pytest.mark.skipif(PS is None, reason="powershell not on PATH")
def test_choco_install_bad_variant_refused(tmp_path):
    rendered = tmp_path / "rendered"
    render_choco.render("0.1.0", "ab" * 32, rendered)
    ps1 = rendered / "tools" / "chocolateyinstall.ps1"
    harness = f"""
$env:LOCALAPPDATA = '{tmp_path}'
$env:ChocolateyPackageParameters = '/Variant:bogus'
function global:Install-ChocolateyPackage {{ throw 'must not be called' }}
try {{ . '{ps1}'; 'no-error' }} catch {{ 'caught' }}
"""
    r = _ps_dot_source(harness)
    assert "caught" in r.stdout
    assert "no-error" not in r.stdout


# ---- contracts ------------------------------------------------------------


def test_choco_install_static_contract():
    # the setup.exe is verified via Install-ChocolateyPackage checksum —
    # no unverified download path is allowed in the script.
    assert "checksumType" in INSTALL_PS1 and "sha256" in INSTALL_PS1
    assert "Invoke-WebRequest" not in INSTALL_PS1
    assert "iwr " not in INSTALL_PS1 and "curl " not in INSTALL_PS1
    # per-user install only — nothing elevated or network-opening
    assert "/CURRENTUSER" in INSTALL_PS1
    for bad in ("netsh", "New-NetFirewall", "0.0.0.0"):
        assert bad not in INSTALL_PS1


def test_choco_uninstall_static_contract():
    # silent Inno uninstall, AppId-keyed, data folder never deleted
    assert "/VERYSILENT" in UNINSTALL_PS1
    assert "_is1" in UNINSTALL_PS1
    assert "QuietUninstallString" in UNINSTALL_PS1
    for bad in ("Remove-Item", "rmtree", "DelTree", "data"):
        if bad == "data":
            continue
        assert bad not in UNINSTALL_PS1, bad
    # the only "data" mention is the keep-data comment
    for line in UNINSTALL_PS1.splitlines():
        if "data" in line.lower():
            assert line.lstrip().startswith("#")
