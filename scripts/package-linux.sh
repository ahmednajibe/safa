#!/bin/sh
# package-linux.sh — .deb / .rpm / AppImage for Linux x86_64 (M10d).
# Every subcommand reuses the portable stage tarball from
# package-unix.sh (cpu and cuda variants) and rearranges it into the
# package layout — never downloads a runtime itself.
#
#   scripts/package-linux.sh deb      [--variant cpu|cuda] [--dry-run]
#   scripts/package-linux.sh rpm      [--variant cpu|cuda] [--dry-run]
#   scripts/package-linux.sh appimage [--dry-run]
#   scripts/package-linux.sh all      [--dry-run]
#
# Layout (deb/rpm): /opt/safa payload + /usr/bin/safa wrapper (sets
# SOUND_SYSTEM_DATA_DIR → ${XDG_DATA_HOME}/safa-data — the app dir is
# root-owned) + .desktop + icon. ffmpeg is BUNDLED (tools/ffmpeg), so
# no Depends/Requires on a system ffmpeg: Fedora base lacks a full
# ffmpeg and an AppImage cannot assume one exists. postrm/%postun never
# delete user data.
#
# AppImage: CPU variant only — the CUDA payload is ~4.9 GB, far over
# the 2 GiB release-asset limit. AppRun sets the same XDG data dir.
#
# Tools: dpkg-deb, rpmbuild, appimagetool are used when present; on
# Windows/dev machines this script runs with --dry-run for lint.
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

CMD="${1:-}"; [ $# -gt 0 ] && shift
VARIANT="cpu"
DRY=0
TOOL=""
while [ $# -gt 0 ]; do
    case "$1" in
        --variant) VARIANT="$2"; shift 2 ;;
        --tool) TOOL="$2"; shift 2 ;;
        --dry-run) DRY=1; shift ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done

VER="$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -1)"
VER="${VER:-0.1.0}"
REPO="$(sed -n 's/^repo_url = "\(.*\)"/\1/p' release-info.toml | head -1)"
REPO="${REPO:-https://github.com/<owner>/safa}"
KEY="linux-x64-$VARIANT"
NAME="Safa-$VER-$KEY"
TARBALL="dist_pkg/$NAME.tar.gz"
STAGE_DIR="dist_pkg/$NAME"
ICON_SRC="packaging/windows/launcher/make_icon.py"

say() { echo "[package-linux:$CMD] $*"; }

# Extract the portable tarball into a scratch dir (or reuse the stage
# dir if package-unix --keep-stage left one). Echoes the payload path.
prepare_stage() {
    if [ -d "$STAGE_DIR" ]; then echo "$STAGE_DIR"; return; fi
    [ -f "$TARBALL" ] || { echo "missing $TARBALL — run scripts/package-unix.sh --variant $VARIANT first" >&2; exit 1; }
    D="data/tmp/pkg-$KEY"
    rm -rf "$D"; mkdir -p "$D"
    tar -xzf "$TARBALL" -C "$D"
    echo "$D/$NAME"
}

icon_png() {
    python3 "$ICON_SRC" --png "$1" --size 256 2>/dev/null \
        || uv run --no-sync python "$ICON_SRC" --png "$1" --size 256
}

check_payload() {
    P="$1"
    for rel in python/bin/python3 backend/launcher.py frontend/dist \
               tools/ffmpeg/bin; do
        [ -e "$P/$rel" ] || { echo "stage payload missing $rel" >&2; exit 1; }
    done
    # system packages use installed mode (env) — never ship the marker
    [ ! -e "$P/portable.txt" ] || rm -f "$P/portable.txt"
}

# ---- deb -------------------------------------------------------------------

build_deb() {
    PKG="safa"; CONFLICT="safa-cuda"
    [ "$VARIANT" = "cuda" ] && PKG="safa-cuda" && CONFLICT="safa"
    OUT="dist_pkg/$NAME.deb"
    say "deb $OUT (pkg=$PKG)"
    if [ "$DRY" = "1" ]; then
        say "[dry-run] extract $TARBALL → /opt/safa, wrapper → /usr/bin/safa,"
        say "[dry-run] desktop + icon → /usr/share, control → DEBIAN/,"
        say "[dry-run] dpkg-deb --root-owner-group --build → $OUT"
        return
    fi
    command -v dpkg-deb >/dev/null 2>&1 || {
        echo "dpkg-deb not found — build in CI or install dpkg" >&2; exit 1; }
    PAYLOAD="$(prepare_stage)"
    check_payload "$PAYLOAD"
    DEB="data/tmp/deb-$KEY"
    rm -rf "$DEB"
    mkdir -p "$DEB/DEBIAN" "$DEB/opt/safa" "$DEB/usr/bin" \
        "$DEB/usr/share/applications" "$DEB/usr/share/icons/hicolor/256x256/apps"
    cp -a "$PAYLOAD/." "$DEB/opt/safa/"
    rm -f "$DEB/opt/safa/portable.txt"
    install -m755 packaging/linux/safa "$DEB/usr/bin/safa"
    install -m644 packaging/linux/safa.desktop "$DEB/usr/share/applications/safa.desktop"
    icon_png "$DEB/usr/share/icons/hicolor/256x256/apps/safa.png"
    SIZE_KB="$(du -sk "$DEB/opt/safa" | cut -f1)"
    sed -e "s|{{PKG}}|$PKG|g" -e "s|{{VERSION}}|$VER|g" \
        -e "s|{{SIZE_KB}}|$SIZE_KB|g" -e "s|{{CONFLICT}}|$CONFLICT|g" \
        packaging/linux/control.template > "$DEB/DEBIAN/control"
    install -m755 packaging/linux/postinst "$DEB/DEBIAN/postinst"
    install -m755 packaging/linux/postrm "$DEB/DEBIAN/postrm"
    dpkg-deb --root-owner-group --build "$DEB" "$OUT"
    ls -lh "$OUT"
}

# ---- rpm -------------------------------------------------------------------

build_rpm() {
    PKG="safa"; [ "$VARIANT" = "cuda" ] && PKG="safa-cuda"
    OUT_DIR="dist_pkg/rpm"
    say "rpm → $OUT_DIR (pkg=$PKG)"
    if [ "$DRY" = "1" ]; then
        say "[dry-run] rpmbuild -bb packaging/rpm/safa.spec"
        say "[dry-run]   --define 'safapkg $PKG' 'safaver $VER' 'safavariant $VARIANT'"
        return
    fi
    command -v rpmbuild >/dev/null 2>&1 || {
        echo "rpmbuild not found — build in CI (fedora container)" >&2; exit 1; }
    [ -f "$TARBALL" ] || { echo "missing $TARBALL — run scripts/package-unix.sh first" >&2; exit 1; }
    RPMBUILD="data/tmp/rpmbuild-$KEY"
    rm -rf "$RPMBUILD"
    for d in BUILD BUILDROOT RPMS SOURCES SPECS SRPMS; do
        mkdir -p "$RPMBUILD/$d"; done
    cp "$TARBALL" "$RPMBUILD/SOURCES/"
    cp packaging/linux/safa packaging/linux/safa.desktop "$RPMBUILD/SOURCES/"
    icon_png "$RPMBUILD/SOURCES/safa.png"
    TOPDIR="$(cd "$RPMBUILD" && pwd)"
    rpmbuild -bb packaging/rpm/safa.spec \
        --define "safapkg $PKG" --define "safaver $VER" \
        --define "safavariant $VARIANT" \
        --define "_topdir $TOPDIR"
    mkdir -p "$OUT_DIR"
    # rename to the release-asset name (rpmbuild emits <pkg>-<ver>-1.*.rpm)
    find "$TOPDIR/RPMS" -name '*.rpm' -exec cp {} "$OUT_DIR/$NAME.rpm" \;
    ls -lh "$OUT_DIR"
}

# ---- appimage ---------------------------------------------------------------

build_appimage() {
    if [ "$VARIANT" = "cuda" ]; then
        say "AppImage is CPU-only — the CUDA payload (~4.9 GB) exceeds"
        say "the 2 GiB release-asset limit; use the .deb/.rpm instead."
        exit 2
    fi
    OUT="dist_pkg/Safa-$VER-linux-x64.AppImage"
    say "appimage $OUT"
    if [ "$DRY" = "1" ]; then
        say "[dry-run] extract $TARBALL → Safa.AppDir/lib/safa,"
        say "[dry-run] AppRun + safa.desktop + safa.png → AppDir root,"
        say "[dry-run] appimagetool --appimage-extract-and-run? no —"
        say "[dry-run] appimagetool Safa.AppDir → $OUT"
        return
    fi
    AT="$TOOL"
    [ -z "$AT" ] && AT="$(command -v appimagetool || true)"
    [ -n "$AT" ] || {
        echo "appimagetool not found — pass --tool <path> or build in CI" >&2
        exit 1; }
    PAYLOAD="$(prepare_stage)"
    check_payload "$PAYLOAD"
    ADIR="data/tmp/appdir-$KEY/Safa.AppDir"
    rm -rf "$ADIR"
    mkdir -p "$ADIR/lib/safa"
    cp -a "$PAYLOAD/." "$ADIR/lib/safa/"
    rm -f "$ADIR/lib/safa/portable.txt"
    install -m755 packaging/appimage/AppRun "$ADIR/AppRun"
    install -m644 packaging/appimage/safa.desktop "$ADIR/safa.desktop"
    icon_png "$ADIR/safa.png"
    "$AT" "$ADIR" "$OUT"
    chmod +x "$OUT"
    ls -lh "$OUT"
}

# ---- dispatch ----------------------------------------------------------------

case "$CMD" in
    deb) build_deb ;;
    rpm) build_rpm ;;
    appimage) build_appimage ;;
    all) build_deb; build_rpm; VARIANT="cpu"; build_appimage ;;
    *) echo "usage: package-linux.sh {deb|rpm|appimage|all} [--variant cpu|cuda] [--dry-run]" >&2
       exit 2 ;;
esac
