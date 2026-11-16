#!/bin/sh
# package-dmg.sh — build the unsigned macOS arm64 .app + .dmg (M10d).
# Wraps the portable tarball payload from package-unix.sh (macos-arm64)
# in a minimal bundle; no signing, no notarization — README documents
# Right-click → Open. Quarantine is NEVER stripped by this script.
#
#   scripts/package-dmg.sh [--dry-run] [--keep-app]
#
# Output: dist_pkg/Safa-<ver>-macos-arm64.dmg
# Requires macOS (hdiutil); on other platforms use --dry-run.
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

DRY=0
KEEP=0
while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY=1; shift ;;
        --keep-app) KEEP=1; shift ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done

VER="$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -1)"
VER="${VER:-0.1.0}"
NAME="Safa-$VER-macos-arm64"
TARBALL="dist_pkg/$NAME.tar.gz"
APP="dist_pkg/Safa.app"
DMG="dist_pkg/$NAME.dmg"

echo "== package-dmg $NAME =="

if [ "$DRY" = "1" ]; then
cat <<EOF
[dry-run] steps:
  tar -xzf $TARBALL → data/tmp/dmg-stage/
  $APP/Contents/{Info.plist,MacOS/Safa,Resources/app,Resources/icon.icns}
    payload = tarball contents minus portable.txt (installed mode)
    MacOS/Safa sets SOUND_SYSTEM_DATA_DIR=~/Library/Application Support/Safa
  hdiutil create -volname Safa -srcfolder $APP -ov -format UDZO $DMG
EOF
    exit 0
fi

[ "$(uname -s)" = "Darwin" ] || {
    echo "hdiutil needs macOS — build in CI or use --dry-run" >&2; exit 1; }
[ -f "$TARBALL" ] || {
    echo "missing $TARBALL — run scripts/package-unix.sh first" >&2; exit 1; }

set -x
STAGE="data/tmp/dmg-stage"
rm -rf "$STAGE" "$APP"
mkdir -p "$STAGE" "$APP/Contents/MacOS" "$APP/Contents/Resources/app"
tar -xzf "$TARBALL" -C "$STAGE"
PAYLOAD="$STAGE/$NAME"
[ -x "$PAYLOAD/python/bin/python3" ] || { echo "payload missing python" >&2; exit 1; }

cp -a "$PAYLOAD/." "$APP/Contents/Resources/app/"
rm -f "$APP/Contents/Resources/app/portable.txt"   # .app → installed mode

sed "s|{{VERSION}}|$VER|g" packaging/macos/Info.plist \
    > "$APP/Contents/Info.plist"
install -m755 packaging/macos/Safa "$APP/Contents/MacOS/Safa"
python3 packaging/windows/launcher/make_icon.py \
    --icns "$APP/Contents/Resources/icon.icns"

hdiutil create -volname "Safa" -srcfolder "$APP" -ov -format UDZO "$DMG"
[ "$KEEP" = "1" ] || rm -rf "$APP" "$STAGE"
ls -lh "$DMG"
echo "== done =="
