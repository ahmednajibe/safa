#!/bin/sh
# start.command — double-clickable macOS launcher (Terminal opens .command
# files). Defers to start.sh next to it.
cd "$(dirname "$0")" && exec ./start.sh "$@"
