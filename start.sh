#!/bin/sh
# start.sh — Safa launcher shim (Linux/macOS). All logic lives in
# backend/launcher.py; this only picks a Python interpreter:
#   bundled python/bin/python3 (portable/installed) → .venv → uv run.
# POSIX sh — keep it free of bashisms (it may be piped/sourced).
set -u

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$ROOT" || exit 4
export PYTHONUTF8=1

if [ -x "python/bin/python3" ]; then
    PY="python/bin/python3"
elif [ -x ".venv/bin/python" ]; then
    PY=".venv/bin/python"
elif [ -x ".venv/bin/python3" ]; then
    PY=".venv/bin/python3"
elif command -v uv >/dev/null 2>&1; then
    # First run on a dev checkout: `uv run` syncs .venv itself.
    exec uv run --extra "${SAFA_UV_EXTRA:-cpu}" python -m backend.launcher "$@"
else
    echo "No Python runtime found and 'uv' is not installed."
    echo "لم يُعثر على بايثون و«uv» غير مثبت."
    exit 5
fi
exec "$PY" -m backend.launcher "$@"
