#!/bin/sh
# install.sh — Safa one-line installer for macOS + Linux (M10c §9.9/13).
#
#   curl -fsSL https://github.com/ahmednajibe/safa/releases/latest/download/install.sh | sh
#   ./install.sh --variant cuda --dir ~/Apps/Safa --data-dir ~/SafaData
#   ./install.sh --from-dir ./downloads   # use local tarball + SHA256SUMS.txt
#   ./install.sh --dry-run
#
# macOS  : installs into ~/Applications/Safa, writes a Safa.command on the
#          Desktop, removes the quarantine attribute from OUR files only.
# Linux  : installs into ~/.local/share/safa, links ~/.local/bin/safa,
#          writes a .desktop entry.
#
# The downloaded tarball is the portable package (bundled Python +
# wheels + ffmpeg) — the install itself needs no further downloads,
# EXCEPT --variant cuda: no cuda tarball is published (the ~4.9 GB
# payload exceeds the 2 GiB release-asset cap), so the CPU payload is
# installed and the bundled env is then provisioned to cu124 online
# via the tarball's tools/uv + hash-locked requirements-cu124.txt —
# the same mechanism as `safa setup --device cuda`.
# The tarball is ALWAYS verified against SHA256SUMS.txt before extract
# (sha256sum, or shasum -a 256 on macOS); missing sums, a missing entry
# or a mismatch aborts with a bilingual error.
set -eu

fail_bilingual() {
    echo "Safa: $1" >&2
    echo "صفا: $2" >&2
    exit 1
}

sha256_file() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{print $1}'
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$1" | awk '{print $1}'
    else
        return 127
    fi
}

APP="Safa"
REPO="https://github.com/ahmednajibe/safa"
VERSION="latest"
VARIANT="auto"
DIR=""
DATA=""
DRY=0
FROM_DIR=""

while [ $# -gt 0 ]; do
    case "$1" in
        --variant) VARIANT="$2"; shift 2 ;;
        --dir) DIR="$2"; shift 2 ;;
        --data-dir) DATA="$2"; shift 2 ;;
        --version) VERSION="$2"; shift 2 ;;
        --repo) REPO="$2"; shift 2 ;;
        --from-dir) FROM_DIR="$2"; shift 2 ;;
        --dry-run) DRY=1; shift ;;
        -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done

OS="$(uname -s)"
ARCH="$(uname -m)"
case "$OS" in
    Darwin)
        [ "$ARCH" = "arm64" ] || { echo "Safa: Apple Silicon required (got $ARCH)" >&2; exit 2; }
        DIR="${DIR:-$HOME/Applications/Safa}"
        DATA="${DATA:-$HOME/Library/Application Support/Safa}"
        ASSET_KEY="macos-arm64"
        ;;
    Linux|MINGW*|MSYS*|CYGWIN*)
        [ "$ARCH" = "x86_64" ] || { echo "Safa: x86_64 required (got $ARCH)" >&2; exit 2; }
        DIR="${DIR:-$HOME/.local/share/safa}"
        DATA="${DATA:-$HOME/.local/share/safa-data}"
        # No cuda tarball is published (the ~4.9 GB payload exceeds the
        # 2 GiB asset cap): cuda installs the CPU payload then
        # provisions cu124 online from the hashed lock — the same path
        # `safa setup --device cuda` takes.
        ASSET_KEY="linux-x64-cpu"
        ;;
    *) echo "Safa: unsupported OS $OS" >&2; exit 2 ;;
esac

case "$VARIANT" in auto|cpu|cuda) ;; *) echo "--variant cpu|cuda" >&2; exit 2 ;; esac
[ "$OS" = "Darwin" ] && VARIANT="cpu"   # no CUDA on macOS
[ "$VARIANT" = "auto" ] && VARIANT="cpu"

if [ "$VERSION" = "latest" ]; then
    BASE_URL="$REPO/releases/latest/download"
    # "latest" release assets keep the versioned filename — resolve the
    # tag via the redirect target (no API call needed).
    if [ -z "$FROM_DIR" ] && [ "$DRY" != "1" ]; then
        RESOLVED="$(curl -fsSIL -o /dev/null -w '%{url_effective}' \
            "$REPO/releases/latest" 2>/dev/null || true)"
        RESOLVED="${RESOLVED##*/tag/}"
        case "$RESOLVED" in
            v*) VERSION="${RESOLVED#v}" ;;
            *) : ;;
        esac
    fi
else
    BASE_URL="$REPO/releases/download/v$VERSION"
fi
TARBALL="Safa-${VERSION}-${ASSET_KEY}.tar.gz"
if [ -n "$FROM_DIR" ] && [ "$VERSION" = "latest" ]; then
    # local mode: pick the one matching tarball in the directory.
    TARBALL="$(ls "$FROM_DIR"/Safa-*-"$ASSET_KEY".tar.gz 2>/dev/null | \
        head -1 | xargs -n1 basename 2>/dev/null || true)"
    [ -n "$TARBALL" ] || fail_bilingual \
        "no Safa-*-$ASSET_KEY.tar.gz in $FROM_DIR" \
        "لا يوجد ملف صفا في $FROM_DIR"
fi

echo "== Safa install =="
echo "dir=$DIR"
echo "data=$DATA"
echo "tarball=${FROM_DIR:-$BASE_URL}/$TARBALL"

if [ "$DRY" = "1" ]; then
    echo "[dry-run] would: download $TARBALL, extract to $DIR,"
    echo "  write $DIR/installed.json -> $DATA, create launcher + desktop entry"
    [ "$VARIANT" = "cuda" ] && echo \
        "  + provision cu124 into $DIR/python via tools/uv + requirements-cu124.txt"
    exit 0
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "== download =="
if [ -n "$FROM_DIR" ]; then
    cp "$FROM_DIR/$TARBALL" "$TMP/$TARBALL" 2>/dev/null || \
        fail_bilingual \
            "tarball not found: $FROM_DIR/$TARBALL" \
            "الملف غير موجود: $FROM_DIR/$TARBALL"
    cp "$FROM_DIR/SHA256SUMS.txt" "$TMP/SHA256SUMS.txt" 2>/dev/null || \
        fail_bilingual \
            "SHA256SUMS.txt missing in $FROM_DIR — refusing to install" \
            "ملف البصمة مفقود — أوقف التثبيت"
else
    curl -fsSL "$BASE_URL/$TARBALL" -o "$TMP/$TARBALL" || \
        fail_bilingual \
            "download failed: $BASE_URL/$TARBALL" \
            "فشل التنزيل: $BASE_URL/$TARBALL"
    curl -fsSL "$BASE_URL/SHA256SUMS.txt" -o "$TMP/SHA256SUMS.txt" || \
        fail_bilingual \
            "could not download SHA256SUMS.txt" \
            "تعذر تنزيل ملف البصمة"
fi

echo "== verify =="
[ -f "$TMP/SHA256SUMS.txt" ] || fail_bilingual \
    "SHA256SUMS.txt missing — refusing to install" \
    "ملف البصمة مفقود — أوقف التثبيت"
EXPECTED="$(awk -v f="$TARBALL" \
    '{n=$2; sub(/^\*/,"",n); sub(/^\.\//,"",n)} n==f{print $1}' \
    "$TMP/SHA256SUMS.txt")"
[ -n "$EXPECTED" ] || fail_bilingual \
    "no checksum entry for $TARBALL — refusing to install" \
    "لا توجد بصمة للملف — أوقف التثبيت"
GOT="$(sha256_file "$TMP/$TARBALL")" || fail_bilingual \
    "no SHA-256 tool available (need sha256sum or shasum)" \
    "لا تتوفر أداة بصمة (sha256sum أو shasum)"
if [ "$GOT" != "$EXPECTED" ]; then
    rm -f "$TMP/$TARBALL"
    fail_bilingual \
        "checksum mismatch for $TARBALL — download deleted, retry" \
        "عدم تطابق البصمة — حُذف التنزيل، أعد المحاولة"
fi
echo "sha256 ok: $GOT"

echo "== extract =="
mkdir -p "$DIR"
tar -xzf "$TMP/$TARBALL" -C "$TMP"
inner="$(find "$TMP" -mindepth 1 -maxdepth 1 -type d | head -1)"
cp -R "$inner/." "$DIR/"
# The tarball is a PORTABLE package — its portable.txt would override
# installed.json and pin data inside the install dir (paths.py). This
# is an installed layout: the marker goes, installed.json wins.
rm -f "$DIR/portable.txt"
chmod -R u+rwX "$DIR"
# Executables the OS must see as executable (git/zips can drop the bit).
chmod +x "$DIR"/tools/ffmpeg/bin/* 2>/dev/null || true
[ -f "$DIR/python/bin/python3" ] && chmod +x "$DIR/python/bin/python3" "$DIR/python/bin/python" 2>/dev/null || true

if [ "$OS" = "Darwin" ]; then
    # Quarantine only OUR extracted tree — never the rest of the disk.
    xattr -dr com.apple.quarantine "$DIR" 2>/dev/null || true
    cat > "$HOME/Desktop/Safa.command" <<EOF
#!/bin/sh
cd "$DIR"
exec "$DIR/python/bin/python3" -m backend.launcher
EOF
    chmod +x "$HOME/Desktop/Safa.command"
else
    mkdir -p "$HOME/.local/bin" "$HOME/.local/share/applications"
    cat > "$HOME/.local/bin/safa" <<EOF
#!/bin/sh
cd "$DIR"
exec "$DIR/python/bin/python3" -m backend.launcher "\$@"
EOF
    chmod +x "$HOME/.local/bin/safa"
    cat > "$HOME/.local/share/applications/safa.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Safa (صفا)
Comment=Clean audio and video on your own computer
Exec=$HOME/.local/bin/safa
Terminal=true
Categories=AudioVideo;
EOF
fi

mkdir -p "$DATA"
printf '{"data_dir":"%s","variant":"%s"}\n' \
    "$(printf '%s' "$DATA" | sed 's/\\/\\\\/g;s/"/\\"/g')" "$VARIANT" \
    > "$DIR/installed.json"

if [ "$VARIANT" = "cuda" ]; then
    echo "== CUDA 12.4 runtime =="
    # Online provisioning — the cuda tarball never ships. The payload
    # carries tools/uv + the hash-locked requirements; uv installs the
    # cu124 wheels straight into the bundled python, like
    # `safa setup --device cuda` does.
    PYBIN="$DIR/python/bin/python3"
    UVBIN="$DIR/tools/uv/uv"
    [ -x "$UVBIN" ] || UVBIN="$(command -v uv || true)"
    [ -x "$PYBIN" ] || fail_bilingual \
        "bundled python missing at $PYBIN" \
        "بايثون المرفق غير موجود في $PYBIN"
    [ -n "$UVBIN" ] || fail_bilingual \
        "uv is required for the CUDA runtime (bundled copy missing, none on PATH)" \
        "أداة uv مطلوبة لتثبيت نمط البطاقة"
    [ -f "$DIR/requirements-cu124.txt" ] || fail_bilingual \
        "requirements-cu124.txt missing in $DIR" \
        "ملف متطلبات CUDA مفقود في $DIR"
    mkdir -p "$DATA/tmp/uv-cache"
    UV_CACHE_DIR="$DATA/tmp/uv-cache" TMPDIR="$DATA/tmp" \
        "$UVBIN" pip install --no-config --no-deps --python "$PYBIN" --system \
            -r "$DIR/requirements-cu124.txt" --require-hashes \
            --extra-index-url "https://download.pytorch.org/whl/cu124" \
            --index-strategy unsafe-best-match \
        || fail_bilingual \
            "CUDA runtime download failed — check the connection and re-run install.sh" \
            "فشل تنزيل بيئة البطاقة — تحقق من الاتصال وأعد المحاولة"
fi

echo "== done =="
echo "Safa installed to $DIR"
echo "Start: ${OS:+$( [ "$OS" = "Darwin" ] && echo "Desktop/Safa.command" || echo "~/.local/bin/safa" )}"
