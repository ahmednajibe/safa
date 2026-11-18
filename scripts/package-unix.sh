#!/bin/sh
# package-unix.sh — build the portable tarball on macOS/Linux (M10c §9.10/14).
# CI runs this on the target OS; on Windows it's lint + --dry-run only.
#
#   scripts/package-unix.sh --variant cpu|cuda [--dry-run] [--keep-stage]
#
# Output: dist_pkg/Safa-<ver>-<macos-arm64|linux-x64-cpu|linux-x64-cuda>.tar.gz
#
# The tarball nests one top dir (the same name); the layout mirrors the
# Windows portable package: python/ (standalone 3.11 with every wheel
# installed via `uv pip install --require-hashes` — same flow as
# package.ps1), backend/, frontend/dist/, tools/ffmpeg/, tools/uv/,
# requirements-{cpu,cu124}.txt (the hashed locks — install.sh --variant
# cuda uses the cu124 one to upgrade the bundled env online), scripts/,
# READMEs, start.sh, portable.txt.
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
KEY="unknown"; EXTRA="$VARIANT"
case "$OS" in
    Darwin) [ "$ARCH" = "arm64" ] || { echo "apple silicon only" >&2; exit 2; }
            KEY="macos-arm64"; EXTRA="cpu" ;;
    Linux)  [ "$ARCH" = "x86_64" ] || { echo "x86_64 only" >&2; exit 2; }
            [ "$VARIANT" = "cuda" ] || VARIANT="cpu"
            KEY="linux-x64-$VARIANT"
            EXTRA=$( [ "$VARIANT" = "cuda" ] && echo cu124 || echo cpu ) ;;
    *)      echo "unsupported OS $OS (run on macOS/Linux; on Windows use --dry-run)" >&2
            [ "$DRY" = "1" ] || exit 2 ;;
esac

VER="$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -1)"
VER="${VER:-0.1.0}"
NAME="Safa-$VER-$KEY"
STAGE="dist_pkg/$NAME"
PYROOT="data/tmp/pyroot-$KEY"

echo "== package-unix $NAME (extra=$EXTRA) =="

if [ "$DRY" = "1" ]; then
cat <<EOF
[dry-run] steps:
  scripts/export_requirements.py --extra cpu|cu124 -> \$STAGE/requirements-<extra>.txt (hashed)
  uv python install 3.11 --install-dir $PYROOT -> copy into \$STAGE/python
  uv pip install --no-config --no-deps --python \$STAGE/python/bin/python3 --system \
      -r \$STAGE/requirements-$EXTRA.txt --require-hashes \
      --extra-index-url https://download.pytorch.org/whl/$EXTRA \
      --index-strategy unsafe-best-match
  copy backend/ frontend/dist/ tools/ffmpeg/ tools/uv/ scripts/ README* LICENSE* -> $STAGE
  write portable.txt + LICENSE + THIRD_PARTY_NOTICES
  chmod +x python/bin/* tools/ffmpeg/bin/* tools/uv/uv
  tar -czf dist_pkg/$NAME.tar.gz -C dist_pkg $NAME
EOF
    exit 0
fi

set -x
mkdir -p dist_pkg data/tmp
rm -rf "$STAGE"; mkdir -p "$STAGE"

# Hashed requirements for both variants — the install step installs
# $EXTRA's; BOTH requirements-cpu.txt and requirements-cu124.txt ship
# in the tarball so install.sh --variant cuda can provision the cu124
# runtime online (the cuda tarball itself can never publish).
# scripts/export_requirements.py = uv export + the cpu onnxruntime swap.
uv run --no-project python scripts/export_requirements.py --extra cpu   -o "$STAGE/requirements-cpu.txt"
uv run --no-project python scripts/export_requirements.py --extra cu124 -o "$STAGE/requirements-cu124.txt"

# Standalone Python 3.11 under $STAGE/python — same flow as package.ps1.
uv python install 3.11 --install-dir "$PYROOT"
PYSRC="$(find "$PYROOT" -maxdepth 1 -name 'cpython-3.11.*' -type d \
    | sort -r | head -1)"
[ -n "$PYSRC" ] || { echo "no cpython-3.11 install under $PYROOT" >&2; exit 1; }
mkdir -p "$STAGE/python"
cp -R "$PYSRC/." "$STAGE/python/"
rm -f "$STAGE"/python/lib/python*/EXTERNALLY-MANAGED
rm -rf "$PYROOT"
PY="$STAGE/python/bin/python3"
[ -x "$PY" ] || { echo "bundled python missing at $PY" >&2; exit 1; }

# Wheels straight into the bundled python — hash-verified; the pytorch
# index serves the +cpu/+cu124 builds ([tool.uv.sources] is project-only).
# --no-config: pyproject's [tool.uv] override would drop onnxruntime.
uv pip install --no-config --no-deps --python "$PY" --system \
    -r "$STAGE/requirements-$EXTRA.txt" --require-hashes \
    --extra-index-url "https://download.pytorch.org/whl/$EXTRA" \
    --index-strategy unsafe-best-match

# app payload
for d in backend scripts docs; do
    mkdir -p "$STAGE/$d"
    cp -R "$d/." "$STAGE/$d/"
done
mkdir -p "$STAGE/frontend"
cp -R frontend/dist "$STAGE/frontend/"
mkdir -p "$STAGE/tools/ffmpeg/bin"
cp tools/ffmpeg/bin/ffmpeg tools/ffmpeg/bin/ffprobe "$STAGE/tools/ffmpeg/bin/"
# uv rides inside the payload — install.sh --variant cuda uses it to
# provision the cu124 runtime online.
mkdir -p "$STAGE/tools/uv"
UVBIN="$(command -v uv || true)"
[ -n "$UVBIN" ] || { echo "uv not on PATH" >&2; exit 1; }
cp "$UVBIN" "$STAGE/tools/uv/uv"
cp README.md README.ar.md LICENSE THIRD_PARTY_NOTICES.txt start.sh start.command "$STAGE/" 2>/dev/null || true
: > "$STAGE/portable.txt"

find "$STAGE" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
chmod +x "$STAGE/python/bin/"* "$STAGE/tools/ffmpeg/bin/"* "$STAGE/tools/uv/uv" 2>/dev/null || true

tar -czf "dist_pkg/$NAME.tar.gz" -C dist_pkg "$NAME"
[ "$KEEP" = "1" ] || rm -rf "$STAGE"
ls -lh "dist_pkg/$NAME.tar.gz"
echo "== done =="
