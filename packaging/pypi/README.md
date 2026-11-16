# safa-audio — Safa (صفا) bootstrap

`pip install safa-audio` (or `uv tool install safa-audio`) gives you a
small `safa` command that provisions and launches the full Safa app —
local audio/video cleanup on your own computer. Nothing leaves your
machine; the app binds `127.0.0.1` only.

```sh
safa setup --device auto   # cpu|cuda; downloads ~2 GB (cpu) / ~5 GB (cuda)
safa                       # launches http://127.0.0.1:8790
safa models import Safa-models-core-<ver>.zip   # offline model pack
```

The wheel carries the app code, the prebuilt frontend and the frozen,
hash-locked requirement sets — **no** torch, **no** AI models. `safa
setup` builds the heavy runtime under your user-data dir
(`%LOCALAPPDATA%\Safa`, `~/Library/Application Support/Safa`, or
`${XDG_DATA_HOME:-~/.local/share}/safa-data` — `SOUND_SYSTEM_DATA_DIR`
overrides) using `uv` when available, else `pip` with
`--require-hashes`.

ffmpeg: copied from `--ffmpeg-dir` or `PATH` when found, else a pinned,
sha256-verified static build is downloaded (Windows/Linux). On macOS use
`brew install ffmpeg` first.

First run downloads ~600 MB of AI models (resumable); afterwards Safa
works fully offline.

Unsigned/local: no telemetry, no cloud, no open ports.
