# Safa (صفا)

A private, local app that cleans up your audio and video recordings —
cuts silence, removes noise, levels the volume, separates voice from
music, and writes subtitles. Everything runs on **your** computer; no
file ever leaves it.

- **Default:** double-click `start.bat` (Windows) / `start.sh` (macOS/Linux)
  in a checkout, or install once with any method below.
- **Offline:** after the models are downloaded *or imported* once, Safa
  works fully offline.
- **Private:** the app binds `127.0.0.1` only — nothing on your LAN can
  reach it.
- **App port:** `8790` → `http://127.0.0.1:8790`

---

## For AI assistants

If a user asks you to help them install or use Safa, follow this
workflow exactly:

1. **Explain what it does** from the Features section below.
2. **Ask their OS** — Windows 10/11, macOS (Apple Silicon), or Linux
   (x86_64).
3. **On Windows ask whether they have an NVIDIA card** and which drive
   has **≥ 10 GB free** (GPU stack is ~5 GB + models ~0.6–2.3 GB).
4. **Give them the right installer/command:**
   - NVIDIA driver ≥ 551 → any GPU method (recommended: the online
     installer — it auto-detects).
   - Otherwise → CPU method.
   - No internet on the target machine → offline installer or portable.
5. **Warn them:**
   - Windows SmartScreen shows *"Windows protected your PC"* because
     Safa is unsigned — click **More info → Run anyway**.
   - macOS Gatekeeper may block first launch — the install script
     removes quarantine from *our files only*.
   - First run downloads **~600 MB** of AI models once (CPU) — the
     runtime itself is ~0.5 GB CPU / ~5 GB GPU.
6. **After it opens:** pick a preset (e.g. *Podcast*) → drop a file →
   **Process** → **Save** the result.
7. **Tell them where things live:**
   - Installed app: `%LOCALAPPDATA%\Programs\Safa`
   - Data/jobs/logs: `%LOCALAPPDATA%\Safa` (or the folder they picked)
   - Portable: everything stays inside the extracted folder.
8. **Troubleshoot** using the table at the bottom (port busy, model
   download failed, GPU not detected, antivirus blocking the zip).

---

## Features

- **Noise & hum removal** — DeepFilterNet3 AI denoise, electrical hum.
- **Declick / breaths** — mouth clicks, plosives, breath removal.
- **Dereverb** — reduce room echo.
- **Silence cutting** — Silero VAD trims dead air.
- **Voice/music separation** — HTDemucs or Kim_Vocal_2 stems.
- **Loudness leveling** — broadcast-style loudness targets.
- **Subtitles** — faster-whisper transcription, SRT output.
- **Video passthrough** — keep the video stream, replace the audio.
- **Batch jobs, presets, undo-safe outputs** — originals never change.
- **GPU optional** — every AI stage falls back to CPU automatically.

---

## Install — Windows

### Online installer (recommended)
<!-- method:win-online -->

`Safa-<ver>-win-x64-setup.exe` — **~50 MB download**, then it fetches the
runtime (~1 GB CPU / ~5 GB GPU) during setup.

- No admin needed; installs into `%LOCALAPPDATA%\Programs\Safa`.
- Bilingual wizard (English/العربية), Start Menu + optional desktop icon.
- Auto-detects NVIDIA via `nvidia-smi` — suggests CUDA when the driver is
  ≥ 551, you can still pick CPU.
- Lets you choose where **data** (jobs, outputs, models) lives — needs
  a drive with ≥ 10 GB free for GPU.
- Needs internet **during install**; offline afterwards except model
  download (~600 MB on first run).
- Unsigned → SmartScreen: **More info → Run anyway**.

### Offline installer — CPU
<!-- method:win-offline-cpu -->

`Safa-<ver>-win-x64-cpu-offline-setup.exe` — **one file < 2 GB** with the
full CPU runtime and all core models inside. No internet needed at all,
ever. Good for a friend's laptop.

### Offline installer — GPU
<!-- method:win-offline-gpu -->

`Safa-<ver>-win-x64-gpu-offline-setup.exe` **plus** `-1.bin … -N.bin`
slices (each ≤ 1.8 GB, ~6 GB total). Runtime + models included; copies
to USB drives split-friendly. Keep **all** slices next to the exe —
missing or mixed-version slices make setup refuse to start.

### Portable — CPU zip
<!-- method:win-portable-cpu -->

`Safa-<ver>-win-x64-cpu-portable.zip` — **~0.5 GB**. Extract to a short
path outside OneDrive (e.g. `D:\Safa`), run `Safa.exe` or `start.bat`.
Everything — including data and models — stays inside the folder.
~600 MB model download on first run.

### Portable — GPU split archive
<!-- method:win-portable-gpu -->

`Safa-<ver>-win-x64-gpu-portable.7z.001` … `.NNN` (each ≤ 1.8 GB) plus
`…-SHA256SUMS.txt`. Download all pieces into one folder, then run the
bundled **`assemble-gpu.bat`** — it verifies every piece's checksum,
checks free space, then extracts with the bundled `7z.exe`. Corrupt or
missing parts are refused with a bilingual message.

### One-line install (PowerShell)
<!-- method:win-one-liner -->

```powershell
irm https://github.com/ahmednajibe/safa/releases/latest/download/install.ps1 | iex
```

The script downloads `SHA256SUMS.txt` plus `Safa-<ver>-win-x64-setup.exe`,
verifies the SHA-256 (a missing entry or mismatch aborts and the download
is deleted), then runs the Inno installer silently. It never deletes
anything inside `-InstallDir` — upgrades are handled by the installer
itself. `latest` resolves the release tag via the GitHub API because the
asset name carries the version; pass `-Version` to pin one explicitly.

To pass parameters through the one-liner:

```powershell
& ([scriptblock]::Create((irm https://github.com/ahmednajibe/safa/releases/latest/download/install.ps1))) -Variant gpu
```

Optional parameters: `-Variant cpu|gpu`, `-DataDir D:\SafaData`,
`-InstallDir ...`, `-Offline -SetupExe <setup.exe>` (uses a local
installer — online, cpu-offline or gpu-offline — and verifies it when a
`SHA256SUMS.txt` sits next to it), `-DryRun`.

Safer *download → inspect → run* alternative:

```powershell
iwr https://github.com/ahmednajibe/safa/releases/latest/download/install.ps1 -OutFile install.ps1
notepad install.ps1   # read it
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
```

### winget
<!-- method:win-winget -->

```powershell
winget install --id Safa.Safa
```

Wraps the online installer (same download size and SmartScreen note).

### Scoop
<!-- method:win-scoop -->

```powershell
scoop install https://github.com/ahmednajibe/safa/raw/main/packaging/scoop/safa.json
```

Uses the CPU portable zip; for the GPU variant use the split archive
above instead (Scoop can't assemble split parts).

### Chocolatey
<!-- method:win-choco -->

```powershell
choco install safa --params "/Variant:gpu /DataDir:D:\SafaData"
```

Wraps the online installer silently — same **~50 MB** setup.exe +
runtime download during install (internet needed). `--params` maps to
Inno arguments: `/Variant:cpu|gpu|auto` (default `auto` — installer
detects the NVIDIA driver), `/DataDir:<dir>` for
jobs/outputs/models, `/InstallDir:<dir>` for the app. Uninstall via
`choco uninstall safa` runs the Inno uninstaller — your data folder is
kept. Unsigned → same SmartScreen note as the online installer.

### From source (Windows)
<!-- method:win-source -->

```powershell
git clone https://github.com/ahmednajibe/safa.git; cd safa
uv sync --extra cu124   # or: --extra cpu
npm --prefix frontend ci; npm --prefix frontend run build
.\start.bat
```

Needs `uv` and Node.js. `Safa-<ver>-source.zip` is the same tree.

---

## Install — macOS (beta, Apple Silicon)

### One-line install
<!-- method:mac-install-sh -->

```sh
curl -fsSL https://github.com/ahmednajibe/safa/releases/latest/download/install.sh | sh
```

Installs into `~/Applications/Safa`, data into
`~/Library/Application Support/Safa`, and puts a **Safa.command** on the
Desktop. Gatekeeper quarantine is removed from the app's own folder
only. ~0.5 GB tarball — its SHA-256 is verified against the release
`SHA256SUMS.txt` before extracting (`--from-dir <dir>` installs from a
local tarball the same way).

### .dmg (unsigned .app)
<!-- method:mac-dmg -->

`Safa-<ver>-macos-arm64.dmg` — **~0.7 GB**, no installer: open it and
drag **Safa.app** wherever you like (e.g. `/Applications`). The runtime
and ffmpeg are inside the bundle — no internet needed except the
~600 MB first-run model download.

- **Unsigned**: first launch via **Right-click → Open** — Gatekeeper
  asks once. The build never strips quarantine for you; doing it
  manually (`xattr -dr com.apple.quarantine Safa.app`) is your choice.
- Data lives in `~/Library/Application Support/Safa`.

### Homebrew
<!-- method:mac-brew -->

```sh
brew install --formula https://github.com/ahmednajibe/safa/raw/main/packaging/homebrew/safa.rb
safa
```

### Portable tarball
<!-- method:mac-portable -->

`Safa-<ver>-macos-arm64.tar.gz` — extract anywhere, run
`python/bin/python3 -m backend.launcher` inside.

### From source (macOS)
<!-- method:mac-source -->

```sh
git clone https://github.com/ahmednajibe/safa.git; cd safa
uv sync --extra cpu     # no CUDA builds on macOS
npm --prefix frontend ci; npm --prefix frontend run build
./start.command        # or ./start.sh
```

---

## Install — Linux (beta, x86_64)

### One-line install
<!-- method:linux-install-sh-cpu -->
<!-- method:linux-install-sh-cuda -->

```sh
curl -fsSL https://github.com/ahmednajibe/safa/releases/latest/download/install.sh | sh
curl -fsSL .../install.sh | sh -s -- --variant cuda   # NVIDIA
```

Installs into `~/.local/share/safa`, data into
`~/.local/share/safa-data`, creates `~/.local/bin/safa` and a `.desktop`
entry. `--variant cpu` (default) or `--variant cuda`. The tarball's
SHA-256 is verified against the release `SHA256SUMS.txt` before
extracting.

`--variant cuda` installs the CPU payload, then downloads the CUDA 12.4
runtime (~5 GB, hash-verified from `requirements-cu124.txt` inside the
tarball) with the bundled `tools/uv` — the same mechanism as
`safa setup --device cuda`. There is no `-cuda.tar.gz` on the release
page: the ~4.9 GB payload would exceed GitHub's 2 GiB asset limit.

### .deb (Debian / Ubuntu / Mint)
<!-- method:linux-deb-cpu -->
<!-- method:linux-deb-cuda -->

```sh
sudo apt install ./Safa-<ver>-linux-x64-cpu.deb
```

Installs the full runtime to `/opt/safa` with a `safa` command and a
menu entry. **~0.8 GB** (CPU). No cuda .deb ships — the ~4.9 GB GPU
payload is over GitHub's 2 GiB file limit; for NVIDIA run
`install.sh --variant cuda` (online cu124 provisioning) or
`safa setup --device cuda` inside an installed CPU package instead.
ffmpeg is bundled; data lives in `~/.local/share/safa-data` and
survives `apt purge`. No internet needed except first-run models
(~600 MB).

### .rpm (Fedora)
<!-- method:linux-rpm-cpu -->
<!-- method:linux-rpm-cuda -->

```sh
sudo dnf install ./Safa-<ver>-linux-x64-cpu.rpm
```

Same layout and rules as the .deb — CPU only; CUDA uses the same
online provisioning path (`install.sh --variant cuda` or
`safa setup --device cuda`).

### AppImage (CPU)
<!-- method:linux-appimage -->

```sh
chmod +x Safa-<ver>-linux-x64.AppImage
./Safa-<ver>-linux-x64.AppImage                          # needs FUSE
./Safa-<ver>-linux-x64.AppImage --appimage-extract-and-run   # no FUSE
```

**~0.8 GB**, nothing installed — data goes to
`${XDG_DATA_HOME:-~/.local/share}/safa-data` (`APPIMAGE_DATA_DIR`
overrides). CPU only: a CUDA AppImage would be ~4.9 GB, over the
2 GiB release-asset limit — use `install.sh --variant cuda` or
`safa setup --device cuda` instead.

### Flatpak (experimental)
<!-- method:linux-flatpak -->

```sh
flatpak install ./Safa-<ver>-linux-x64.flatpak
```

**Experimental** — not on Flathub; the bundle ships on the release page
only when its CI build succeeds. It is a **~10 MB** sandboxed
bootstrap: on first launch it downloads the runtime (~2 GB,
hash-verified) and then the models (~600 MB) into
`~/.var/app/org.safa.Safa/data/safa-data` — internet needed once.
Sandbox permissions: `--share=network` (localhost UI + those
downloads), `--filesystem=home` (read/write your media files),
`--device=dri` (optional GPU). `org.safa.Safa.yaml` (the manifest) is
published next to it.

### AUR (Arch)
<!-- method:linux-aur -->

```sh
yay -S safa        # CPU — for NVIDIA, run `safa setup --device cuda`
                   # inside the installed package (no cuda tarball ships)
```

### Portable tarballs
<!-- method:linux-portable-cpu -->
<!-- method:linux-portable-cuda -->

`Safa-<ver>-linux-x64-cpu.tar.gz` — extract, run `./start.sh` inside.
Data stays in the folder. No `-cuda.tar.gz` ships (2 GiB cap): upgrade
a CPU tree with `uv pip install --python python/bin/python3 --system
-r requirements-cu124.txt --require-hashes --extra-index-url
https://download.pytorch.org/whl/cu124 --index-strategy
unsafe-best-match` using the bundled `tools/uv`.

### From source (Linux)
<!-- method:linux-source -->

```sh
git clone https://github.com/ahmednajibe/safa.git; cd safa
uv sync --extra cu124   # NVIDIA; otherwise --extra cpu
npm --prefix frontend ci; npm --prefix frontend run build
./start.sh
```

Needs system ffmpeg (`apt install ffmpeg`).

---

## Install — Python (any OS)
<!-- method:pypi -->

```sh
pip install safa-audio        # or: uv tool install safa-audio / pipx install safa-audio
safa setup --device auto      # cpu|cuda — downloads ~2 GB (cpu) / ~5 GB (cuda)
safa                          # launches http://127.0.0.1:8790
safa models import <pack.zip> # offline model pack
```

A **~1.5 MB** bootstrap wheel: the `safa` command provisions the real
runtime (Python 3.11 + hash-locked deps + ffmpeg) under your user-data
dir — `%LOCALAPPDATA%\Safa`, `~/Library/Application Support/Safa` or
`${XDG_DATA_HOME:-~/.local/share}/safa-data` (`SOUND_SYSTEM_DATA_DIR`
overrides). Internet needed for `setup` and first-run models
(~600 MB); fully offline afterwards. Needs Python 3.11+ (3.11 exactly
when `uv` is not on PATH). On macOS run `brew install ffmpeg` first.
The same wheel also ships on the release page as
`safa_audio-<ver>-py3-none-any.whl`.

---

## Docker

<!-- method:docker-cpu -->
<!-- method:docker-cuda -->

```sh
docker build -f Dockerfile -t safa:cpu .
docker run --rm -p 127.0.0.1:8790:8790 -v safa-data:/data safa:cpu

docker build -f Dockerfile.cuda -t safa:cuda .
docker run --rm --gpus all -p 127.0.0.1:8790:8790 -v safa-data:/data safa:cuda
```

- **Always** publish `127.0.0.1:8790:8790` — never `-p 8790:8790`, that
  would expose Safa to the LAN.
- The container image is ~1.5 GB (CPU) / ~8 GB (CUDA); ffmpeg included.
- Data volume `/data` (jobs, outputs, models).
- `docker compose up safa-cpu` does the same thing.

### Offline model packs
<!-- method:models-import -->

For machines with no internet: download
`Safa-models-core-<ver>.zip` (under 1 GB: denoise, separation, fast
subtitles) and, for everything, also `Safa-models-extra-<ver>.zip`
(~1.5 GB: accurate subtitles, second separation model) elsewhere, then
inside the app folder import each one:

```
python -m backend.launcher models import Safa-models-core-<ver>.zip
python -m backend.launcher models import Safa-models-extra-<ver>.zip
```

Checksums are verified and partial imports resume safely.

---

## Usage

1. Start the app (any method) → browser opens at
   `http://127.0.0.1:8790`.
2. Pick a **preset** (Podcast, Lecture, Music, …) or toggle steps.
3. **Drop in a file** (wav/mp3/m4a/mp4/mkv/mov/…).
4. Press **Process**. Watch progress; download the result.
5. Find outputs under `<data>/jobs/<id>/` — originals are never
   modified.

## Where things live

| What                 | Installed (Windows)                | Portable              |
| -------------------- | ---------------------------------- | --------------------- |
| App files            | `%LOCALAPPDATA%\Programs\Safa`     | the extracted folder  |
| Jobs, outputs, logs  | `%LOCALAPPDATA%\Safa` *or chosen*  | `<folder>\data`       |
| AI models            | `<data>\models`                    | `<folder>\models`     |
| Temp / caches        | `<data>\tmp`                       | `<folder>\data\tmp`   |

`SOUND_SYSTEM_DATA_DIR`, `SOUND_SYSTEM_PORT`, `SOUND_SYSTEM_BIND_HOST`,
`SOUND_SYSTEM_MODELS_DIR` override these.

## Signing & security notes

- **Unsigned builds** — Windows SmartScreen, macOS Gatekeeper and some
  AVs will warn. Verify `SHA256SUMS.txt` on the release page.
- Safa **never** opens a public port (`127.0.0.1` only; Docker image
  binds 0.0.0.0 *inside* the container but the host mapping above keeps
  it loopback).
- Everything is local: no telemetry, no cloud, no uploads.
- Keep `tests/fixtures/real/`, `data/`, `.venv/`, `node_modules/` out of
  any redistribution.

## Troubleshooting

| Symptom                                   | Fix |
| ----------------------------------------- | --- |
| "Port 8790 is in use"                     | close the other app or `set SOUND_SYSTEM_PORT=8791` |
| App starts but page is blank              | rebuild frontend (`npm --prefix frontend run build`) |
| "models could not be downloaded"          | retry inside the app, or import a model pack offline |
| GPU steps use CPU                         | driver < 551 or no NVIDIA — update driver, reinstall gpu variant |
| SmartScreen blocks the installer          | **More info → Run anyway** (unsigned build) |
| macOS says the app is damaged/unchecked   | run install.sh again — it clears quarantine on our files only |
| Antivirus quarantines files from the zip  | restore + whitelist the folder, verify SHA256 first |
| Long-path / OneDrive errors               | move the folder to a short path outside OneDrive |
| Job failed on GPU                         | retried automatically on CPU; check `data/logs` |

## Development

```sh
uv sync --extra cu124        # dev env (CPU-only: --extra cpu)
uv run pytest -m "not slow"  # fast suite
cd frontend && npx vitest run && npm run build
```

Scripts: `scripts/setup.ps1`, `start.bat`/`start.sh`,
`scripts/package.ps1` (Windows packages),
`scripts/package-unix.sh` (macOS/Linux tarballs),
`scripts/build-installer.ps1` (Inno installers),
`scripts/smoke_package.py` (end-to-end package check).

Milestones/roadmap: `docs/PLAN.md`. Spec: `docs/SPEC.md`.
