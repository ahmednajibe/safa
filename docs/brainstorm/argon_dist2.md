Using **brainstorming** and **writing-plans** skills (with security review of artifact integrity and loopback isolation) for this read-only M10c design update.

### 1. Install Method Verdict (All OSes)
* **Windows**
  * **(a) Small online `setup.exe` (Inno + `uv`, NVIDIA auto-detect):** **Keep now** — ~50 MB download, EN/AR wizard, picks CPU/GPU and data drive for non-technical friends.
  * **(b) Full OFFLINE `setup.exe` CPU (~560 MB):** **Keep now** — fits under GitHub’s 2 GiB cap as one file; immune to flaky internet during install.
  * **(c) Full OFFLINE GPU spanned installer (<2 GiB parts):** **Keep now** — needed for friends with NVIDIA GPUs who want zero `uv`/PyPI downloads or install from USB.
  * **(d) Portable ZIPs (`cpu.zip` + split `gpu.zip.001..003`):** **Keep now** — already built by [package.ps1](file:///F:/sound_system/scripts/package.ps1); zero-install extraction to any drive.
  * **(e) `irm …/install.ps1 | iex`:** **Keep now** — best copy-paste path for AI assistants and PowerShell users.
  * **(f) Winget manifest:** **Keep now** — generate `packaging/winget/` YAML in release build (`winget install --manifest` works immediately; submit to `winget-pkgs` after first public tag).
  * **(g) Scoop manifest:** **Keep now** — single `packaging/scoop/safa.json` pointing at the CPU portable zip + SHA256.
  * **(h) Chocolatey:** **Keep later** — redundant with Winget/Scoop and moderated slowly; generate a `.nuspec` stub only if requested.
  * **(i) From source (`uv`):** **Keep now** — already works.
* **macOS**
  * **(j) `install.sh`:** **Keep now** — avoids Gatekeeper "damaged app" popups and creates `~/Applications/Safa.command`.
  * **(k) Homebrew tap (`Formula/safa.rb` + Cask):** **Keep now** — 30-line Ruby file in repo (`brew install --formula packaging/homebrew/safa.rb`).
  * **(l) Unsigned `.dmg` / `.app`:** **Keep now (thin wrapper)** — bundle a lightweight `.app` launcher inside `.dmg`; document `Right-click → Open` / `xattr -dr com.apple.quarantine` (no $99/yr Apple ID needed).
  * **(m) From source:** **Keep now**.
* **Linux**
  * **(n) `install.sh`:** **Keep now** — installs to `~/.local/share/safa` + `~/.local/bin/safa` + `.desktop`.
  * **(o) AppImage:** **Keep later** — FUSE/glibc bundling conflicts with CUDA/ort; `.deb` + `install.sh` + Docker cover Linux cleaner now.
  * **(p) `.deb` package:** **Keep now** — `dpkg-deb --build` staging `/opt/safa` + `/usr/share/applications/safa.desktop` (`Depends: ffmpeg`); high value on Ubuntu/Mint.
  * **(q) Flatpak:** **Drop** — Flathub build sandbox forbids network pip installs and complicates host CUDA visibility.
  * **(r) AUR (`PKGBUILD`):** **Keep now** — ship `packaging/aur/PKGBUILD` in repo (25 lines).
* **Any OS (+ 1 Missing Method)**
  * **(s) Docker (CPU + CUDA) + `docker-compose.yml`:** **Keep now** — reproducible across all OSes/WSL2.
  * **(t) PyPI / `uv tool install` / `pipx`:** **Keep now** — wheel with prebuilt `frontend/dist`.
  * **(u) From source:** **Keep now**.
  * **(v) NEW — `Safa-models-core.zip` (~590 MB) release asset:** **Keep now** — without a downloadable model pack, "offline" installers still require internet on first job.

### 2. Delivering >2 GiB Offline GPU Packages on GitHub Releases
* **Offline GPU Installer:** Use Inno Setup native disk spanning (`DiskSpanning=yes`, `DiskSliceSize=1950000000`), producing `Safa-<ver>-win64-gpu-offline-setup.exe` + `...-1.bin`..`...-3.bin` (≤1.81 GiB each). Users or `install.ps1` put them in one folder and run `.exe`—no 7-Zip required.
* **Offline GPU Portable ZIP:** Split `gpu.zip` into `.zip.001`..`.zip.003` (≤1.8 GiB) + ship **`assemble-gpu.bat`** (uses built-in PowerShell stream concatenation + `SHA256SUMS.txt` check + `Expand-Archive`).

### 3. PyPI (`uv tool` / `pipx`) & Docker Design
* **PyPI / `uv tool`:**
  * **Name risk:** `safa` is likely taken on PyPI; publish as **`safa-audio`** with CLI entry points `safa` and `safa-audio` (`safa = "backend.launcher:main"`).
  * **Wheel layout:** Replace `package = false` in [pyproject.toml](file:///F:/sound_system/pyproject.toml#L45) with `hatchling`, bundling `frontend/dist/**` inside `backend/_static/` (used by [main.py](file:///F:/sound_system/backend/main.py#L33) when `ROOT/frontend/dist` is absent).
  * **Torch CPU vs CUDA:** Default dependencies install CPU `torch==2.5.1` + `onnxruntime==1.20.0`. Extras: `safa-audio[cpu]` vs `safa-audio[cuda]` (adds `onnxruntime-gpu` + `nvidia-*-cu12` wheels, installed with `--extra-index-url https://download.pytorch.org/whl/cu124 --index-strategy unsafe-best-match`).
  * **Paths & offline:** When imported from `site-packages`, [paths.py](file:///F:/sound_system/backend/core/paths.py#L10-L12) defaults `DATA_DIR`/`MODELS_DIR` to the OS user data dir (`%LOCALAPPDATA%\Safa`, `~/.local/share/safa`, `~/Library/Application Support/Safa`). 100% offline after first-run model download or `safa models import Safa-models-core.zip`.
* **Docker:**
  * Multi-stage `Dockerfile` (`node:22-alpine` builds `frontend/dist` → `python:3.11-slim` + `ffmpeg` + `uv sync --frozen --extra cpu|cu124`).
  * **Loopback safety:** Add `SOUND_SYSTEM_BIND_HOST` (default `127.0.0.1`, set to `0.0.0.0` only inside container) in [settings.py](file:///F:/sound_system/backend/core/settings.py#L31) so Docker port forwarding reaches uvicorn, while `docker-compose.yml` binds **`127.0.0.1:8790:8790`** on the host and [main.py](file:///F:/sound_system/backend/main.py#L85)’s `host_origin_guard` still enforces `Host: 127.0.0.1:8790`.
  * **Data/models:** Single volume `-v safa-data:/data` (`SOUND_SYSTEM_DATA_DIR=/data`); offline after first run.

### 4. Verification & Acceptance Tests
* **On this Windows machine:** Build & test `.exe` launcher, Inno online + offline CPU + spanned GPU installers, portable ZIPs + `assemble-gpu.bat`, `install.ps1`, `uv build --wheel` + `uv tool install` from local wheel, `install.sh` dry-run via Git Bash, and manifest generators.
* **In GitHub Actions CI:** Windows (Inno silent lifecycle), Ubuntu (`install.sh`, `.deb` build+install, Docker CPU smoke, wheel install), macOS (`install.sh`, `.app`/`.dmg` build, Homebrew syntax check).
* **Tests to add:**
  * `test_wheel_build_and_cli_entry`: verifies wheel bundles `index.html` and `safa --help` / health works from an isolated venv.
  * `test_gpu_split_assembler_and_tamper`: splits test archive, verifies `assemble-gpu.bat` reconstructs + checks SHA-256 and rejects corrupted parts.
  * `test_inno_online_and_offline_lifecycle`: silent install/upgrade/uninstall in `data\tmp\inst\تجربة`.
  * `test_manifests_and_docker_contract`: asserts Winget/Scoop/Homebrew/AUR/`.deb`/`docker-compose.yml` versions, SHA256s, and `127.0.0.1:8790:8790` stay in sync with [pyproject.toml](file:///F:/sound_system/pyproject.toml#L3).
