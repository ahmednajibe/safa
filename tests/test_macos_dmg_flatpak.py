"""macOS .dmg bundle + Flatpak manifest — static contract tests (M10d).

hdiutil / flatpak-builder don't exist on Windows; the .app assembly and
flatpak build happen in CI. Here: plist validity, launcher semantics,
script dry-run, manifest security surface.
"""
from __future__ import annotations

import plistlib
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MACOS = ROOT / "packaging" / "macos"
FLATPAK = ROOT / "packaging" / "flatpak"
DMG_SH = (ROOT / "scripts" / "package-dmg.sh").read_text("utf-8")
PLIST = (MACOS / "Info.plist").read_text("utf-8")
LAUNCHER = (MACOS / "Safa").read_text("utf-8")
MANIFEST = (FLATPAK / "org.safa.Safa.yaml").read_text("utf-8")
RUN = (FLATPAK / "safa-run.sh").read_text("utf-8")
REL = (ROOT / ".github" / "workflows" / "release.yml").read_text("utf-8")


def _sh(script: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["sh", str(script), *args],
                          capture_output=True, text=True, timeout=30,
                          cwd=ROOT)


# ---- macOS .app -------------------------------------------------------------


def test_info_plist_valid():
    rendered = PLIST.replace("{{VERSION}}", "0.1.0")
    d = plistlib.loads(rendered.encode("utf-8"))
    assert d["CFBundleExecutable"] == "Safa"
    assert d["CFBundleIdentifier"] == "org.safa.Safa"
    assert d["CFBundleVersion"] == "0.1.0"
    assert d["CFBundleIconFile"] == "icon.icns"
    assert "arm64" in d["LSArchitecturePriority"]


def test_macos_launcher_data_dir():
    assert "Library/Application Support/Safa" in LAUNCHER
    assert "SOUND_SYSTEM_DATA_DIR" in LAUNCHER
    assert "Resources/app" in LAUNCHER
    assert "backend.launcher" in LAUNCHER


def test_dmg_script_dry_run():
    r = _sh(ROOT / "scripts" / "package-dmg.sh", "--dry-run")
    assert r.returncode == 0, r.stderr
    for piece in ("hdiutil", "Safa.app", "portable.txt", "UDZO"):
        assert piece in r.stdout, piece


def test_dmg_script_never_strips_quarantine():
    for bad in ("xattr -d", "xattr -c", "com.apple.quarantine",
                "spctl", "codesign", "notarytool"):
        assert bad not in DMG_SH, bad
    # bundle is explicitly unsigned — no signing step at all
    assert "UDZO" in DMG_SH


def test_dmg_removes_portable_marker():
    # .app is installed-mode; portable.txt must not survive the copy
    assert "rm -f" in DMG_SH and "portable.txt" in DMG_SH


def test_make_icon_png_and_icns(tmp_path):
    import sys
    sys.path.insert(0, str(ROOT / "packaging" / "windows" / "launcher"))
    import make_icon
    png = make_icon.write_png(tmp_path / "i.png", 64)
    assert png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    icns = make_icon.write_icns(tmp_path / "i.icns")
    blob = icns.read_bytes()
    assert blob[:4] == b"icns"
    import struct
    assert struct.unpack(">I", blob[4:8])[0] == len(blob)
    assert b"ic08" in blob and b"ic09" in blob


# ---- flatpak ----------------------------------------------------------------


def test_flatpak_manifest_yaml_parses():
    import yaml
    # strip only comment lines — the manifest itself is real yaml
    d = yaml.safe_load("\n".join(
        l for l in MANIFEST.splitlines()
        if not l.lstrip().startswith("#")))
    assert d["app-id"] == "org.safa.Safa"
    assert d["command"] == "safa"
    args = d["finish-args"]
    assert "--share=network" in args      # model download + 127.0.0.1 UI
    assert "--filesystem=home" in args    # media in/out under home
    assert "--device=dri" in args         # optional GPU accel
    # no session/system bus take-overs, no host filesystem
    assert "--filesystem=host" not in args
    assert "--socket=session-bus" not in args


def test_flatpak_uv_hash_pinned():
    import re
    m = re.search(r"uv-[\w.-]+\.tar\.gz\s*\n\s*sha256:\s*([0-9a-f]{64})",
                  MANIFEST)
    assert m, "uv archive must carry a sha256"
    # the committed hash matches the verified 0.12.19 linux binary
    assert m.group(1) == (
        "23bf5552d220e0842b65c862097b2ebaeba0064b74eda5e565e77fd25969d8c8")


def test_flatpak_run_wrapper_data_dir():
    assert "org.safa.Safa/data" in RUN
    assert "SOUND_SYSTEM_DATA_DIR" in RUN
    assert "safa_bootstrap" in RUN
    # heavy runtime provisions INSIDE the sandbox data dir, never /app
    assert "UV_CACHE_DIR" in RUN


def test_flatpak_reverse_dns_explained():
    # the id choice must be justified in a comment (owner is a token)
    comments = "\n".join(l for l in MANIFEST.splitlines()
                         if l.lstrip().startswith("#"))
    assert "reverse-dns" in comments.lower()
    assert "org.safa" in comments


def test_flatpak_runtime_matches_ci_image():
    # the release.yml flatpak job runs inside a freedesktop-XX.YY
    # container — it must track the manifest's runtime-version, and the
    # runtime must not be EOL (24.08 died mid-release, v0.1.1).
    import re
    ver = re.search(r"^runtime-version:\s*'([0-9]+\.[0-9]+)'",
                    MANIFEST, re.M)
    tag = re.search(r"flatpak-github-actions:freedesktop-([0-9]+\.[0-9]+)",
                    REL)
    assert ver, "runtime-version missing from the flatpak manifest"
    assert tag, "freedesktop image tag missing from the flatpak job"
    assert ver.group(1) == tag.group(1)
    assert int(ver.group(1).split(".")[0]) >= 25, \
        "org.freedesktop runtime is end-of-life"


def test_all_posix_scripts_lf():
    for p in (FLATPAK / "safa-run.sh", MACOS / "Safa",
              ROOT / "scripts" / "package-dmg.sh"):
        assert b"\r\n" not in p.read_bytes(), p
