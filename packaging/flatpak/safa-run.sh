#!/bin/sh
# /app/bin/safa inside the flatpak (M10d). Data lives in the flatpak's
# own XDG data dir — ~/.var/app/org.safa.Safa/data/safa-data — which
# survives updates. The wheel's `safa` console script exists too, but
# this wrapper pins the sandboxed data dir first.
set -u
DATA="${XDG_DATA_HOME:-$HOME/.var/app/org.safa.Safa/data}/safa-data"
export SOUND_SYSTEM_DATA_DIR="$DATA"
export UV_CACHE_DIR="$DATA/uv-cache"
export PYTHONUTF8=1
mkdir -p "$DATA"
exec python3 -m safa_bootstrap "$@"
