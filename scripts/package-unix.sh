#!/bin/sh
# package-unix.sh — build the portable tarball on macOS/Linux (M10c §9.10/14).
# CI runs this on the target OS; on Windows it's lint + --dry-run only.
#
#   scripts/package-unix.sh --variant cpu|cuda [--dry-run] [--keep-stage]
#
# Output: dist_pkg/Safa-<ver>-<macos-arm64|linux-x64-cpu|linux-x64-cuda>.tar.gz
#
# The tarball nests one top dir (the same name); the layout mirrors the
# Windows portable package: python/, wheels installed into it, backend/,
# frontend/dist/, tools/ffmpeg/, scripts/, READMEs, start.sh,
# portable.txt.
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

VARIANT="cpu"
DRY=0
KEEP=0
while [ $# -gt 0 ]; do
    case "$1" in
        --variant) VARIANT="$2"; shift 2 ;;
        --dry-run) DRY=1; shift ;;
        --keep-stage) KEEP=1; shift ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done

OS="$(uname -s)"; ARCH="$(uname -m)"
KEY="unknown"; EXTRA="$VARIANT"; PLAT="unknown"
case "$OS" in
    Darwin) [ "$ARCH" = "arm64" ] || { echo "apple silicon only" >&2; exit 2; }
            KEY="macos-arm64"; EXTRA="cpu"; PLAT="aarch64-apple-darwin" ;;
    Linux)  [ "$ARCH" = "x86_64" ] || { echo "x86_64 only" >&2; exit 2; }
            [ "$VARIANT" = "cuda" ] || VARIANT="cpu"
            KEY="linux-x64-$VARIANT"
            EXTRA=$( [ "$VARIANT" = "cuda" ] && echo cu124 || echo cpu )
            PLAT="x86_64-manylinux_2_28" ;;
    *)      echo "unsupported OS $OS (run on macOS/Linux; on Windows use --dry-run)" >&2
            [ "$DRY" = "1" ] || exit 2 ;;
esac

VER="$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -1)"
VER="${VER:-0.1.0}"
NAME="Safa-$VER-$KEY"
STAGE="dist_pkg/$NAME"
REQ="data/tmp/req-$EXTRA-posix.txt"

echo "== package-unix $NAME (extra=$EXTRA platform=$PLAT) =="

if [ "$DRY" = "1" ]; then
cat <<EOF
[dry-run] steps:
  uv export --format requirements-txt --frozen --extra $EXTRA -o $REQ --no-hashes
  uv python install 3.11
  uv pip download -r $REQ --python-version 3.11 --python-platform $PLAT -d $STAGE/wheels
  uv pip install --python <standalone 3.11> -r $REQ --no-index --find-links $STAGE/wheels
  copy backend/ frontend/dist/ tools/ffmpeg/ scripts/{start.sh shim} README* LICENSE* -> $STAGE
  write portable.txt + LICENSE + THIRD_PARTY_NOTICES
  chmod +x python/bin/* tools/ffmpeg/bin/*
  tar -czf dist_pkg/$NAME.tar.gz -C dist_pkg $NAME
EOF
    exit 0
fi

set -x
mkdir -p dist_pkg data/tmp
rm -rf "$STAGE"; mkdir -p "$STAGE/wheels"

uv export --format requirements-txt --frozen --extra "$EXTRA" \
    -o "$REQ" --no-hashes

# Standalone Python 3.11 under $STAGE/python
uv python install 3.11
PYDIR="$(uv python find 3.11 --managed-python | sed 's|/bin/python.*$||;s|/bin$||')"
mkdir -p "$STAGE/python"
cp -R "$PYDIR/." "$STAGE/python/"
PY="$STAGE/python/bin/python3"
[ -x "$PY" ] || PY="$(find "$STAGE/python" -name 'python3' -type f | head -1)"

uv pip download -r "$REQ" --python-version 3.11 --python-platform "$PLAT" \
    -d "$STAGE/wheels"
"$PY" -m ensurepip --upgrade 2>/dev/null || true
"$PY" -m pip install --no-index --find-links "$STAGE/wheels" -r "$REQ"
rm -rf "$STAGE/wheels"

# app payload
for d in backend scripts docs; do
    mkdir -p "$STAGE/$d"
    cp -R "$d/." "$STAGE/$d/"
done
mkdir -p "$STAGE/frontend"
cp -R frontend/dist "$STAGE/frontend/"
mkdir -p "$STAGE/tools/ffmpeg/bin"
cp tools/ffmpeg/bin/ffmpeg tools/ffmpeg/bin/ffprobe "$STAGE/tools/ffmpeg/bin/"
cp README.md README.ar.md LICENSE THIRD_PARTY_NOTICES.txt start.sh start.command "$STAGE/" 2>/dev/null || true
: > "$STAGE/portable.txt"

find "$STAGE" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
chmod +x "$STAGE/python/bin/"* "$STAGE/tools/ffmpeg/bin/"* 2>/dev/null || true

tar -czf "dist_pkg/$NAME.tar.gz" -C dist_pkg "$NAME"
[ "$KEEP" = "1" ] || rm -rf "$STAGE"
ls -lh "dist_pkg/$NAME.tar.gz"
echo "== done =="
