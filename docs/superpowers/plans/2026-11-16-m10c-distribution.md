# M10c — Distribution: .exe, installers per OS, AI-friendly README

Design inputs: `docs/brainstorm/{astra,argon}_dist.md` (prompt: `dist_prompt.md`). Reference only (read-only, do NOT copy files): the user's Wird project `D:\projects\learning` (install/, packaging/, .github/workflows/release.yml, README "For AI assistants"). Decisions below were chosen for THIS app (Python + torch + models), and differ from Wird where noted.

> **For agentic workers:** test-first, do NOT commit, never write/delete outside F:\sound_system (D:\projects\learning is read-only reference), port 8790, tests never write into data/review/ or the system %TEMP%. Do not run GitHub Actions or create any remote/release — write the workflow files only. Product name is the variable `APP_NAME` (default chosen by the orchestrator, see §0).

## 0. Product name
**APP_NAME = Safa (صفا)** — chosen by the user 2026-11-16. Used for exe, installer, shortcuts, data dir, README title. Internal Python package names stay unchanged.

## 1. Measured problem → decision
- M10b artifacts: CPU zip 560 MB (2.16 GB extracted), **GPU zip 4.93 GB (9.25 GB extracted)** — GitHub release assets must be < 2 GiB.
- Astra/Argon proposed split GPU archives. **Decision (orchestrator): ship a small installer that does NOT contain torch/CUDA.** It bundles the app code, prebuilt `frontend/dist`, ffmpeg, a pinned `uv.exe` and a hash-locked dependency lock per variant, and installs the runtime at install time from the official PyPI / download.pytorch.org indexes via `uv sync --frozen` (every wheel hash-checked by uv.lock). Why: one small artifact (~100–150 MB) for every variant, no split archives, GPU/CPU decided on the user's machine, the same mechanism powers install.ps1, install.sh and updates; integrity comes from the lock hashes. Internet is needed at install anyway (models). Offline sharing (USB) keeps the M10b portable full zips built locally (`scripts/package.ps1`), documented, not uploaded.

## 2. Variants in one lock
- Convert `pyproject.toml` to uv **conflicting extras** `cpu` and `cu124` (`[tool.uv] conflicts`, `[tool.uv.sources]` torch/torchaudio per extra, onnxruntime vs onnxruntime-gpu per extra). Dev uses `uv sync --extra cu124`; docs/scripts updated. Keep the existing pins. macOS arm64 resolves `cpu` from PyPI. Verify `uv lock` covers win_amd64, linux x86_64, macOS arm64.

## 3. The .exe (Windows)
- **Tiny native C# launcher** `APP_NAME.exe`, compiled during packaging with the built-in .NET Framework `csc.exe` (present on Windows 10/11; no extra toolchain, no PyInstaller bootloader → fewer antivirus false positives), embedded .ico + version info. (Astra: Go; Argon: C# — C# chosen: zero toolchain.)
- It only starts the private Python: `python\python.exe -m backend.launcher` (or the uv venv's python), visible console window with bilingual status lines, assigns the process tree to a **Job Object with KILL_ON_JOB_CLOSE** (closing the window kills uvicorn/worker/ffmpeg — no orphans).
- **Move launcher logic from `scripts/start.ps1` into `backend/launcher.py`** (cross-platform): port validation, single instance via `/api/health` identity, health wait, open browser, bilingual messages, OneDrive/long-path/low-disk warnings, stale-build/uv-sync checks only in dev mode. `start.bat`, `start.sh`/`start.command` and the exe all call it. Keep the M10 launcher tests passing (port them).

## 4. Install methods
Windows (primary, fully tested):
1. **Installer** `APP_NAME-<ver>-win-x64-setup.exe` (Inno Setup, `PrivilegesRequired=lowest`, `%LOCALAPPDATA%\Programs\APP_NAME`, Start menu + optional desktop shortcut, EN+AR wizard languages). Pages: variant (auto-detect NVIDIA via `nvidia-smi` + driver ≥ 551 for cu124 → recommend GPU, else CPU; user can override) and **data folder** (default `%LOCALAPPDATA%\APP_NAME`, user can pick another drive for a small C:). Runs `uv sync --frozen --extra <variant>` with a progress window; on failure shows a bilingual message and keeps the log.
2. **One line:** `irm <release>/install.ps1 | iex` (same steps, no Inno UI, `-Variant cpu|gpu`, `-DataDir`).
3. **Portable offline zip** (built locally, M10b) for USB sharing.
4. **From source** with uv (`uv sync --extra cpu|cu124`, `npm run build`, start script).
macOS (Apple Silicon) and Linux x86_64 — **beta**, CPU (Linux cu124 optional): `curl -fsSL <release>/install.sh | sh` (`--variant cpu|cuda`): installs into `~/Applications/APP_NAME` (macOS, `.command` launcher on Desktop, quarantine attribute removed on our own files only) or `~/.local/share/APP_NAME` + `~/.local/bin/APP_NAME` + `.desktop` entry (Linux); ffmpeg: use system ffmpeg if ≥ 6, else download a pinned static build with checksum. Mark "beta — tested in CI only" in README until a real machine confirms.
- Cross-platform fixes: `reveal_in_explorer` (`open -R` / `xdg-open`), `gpu_usable()` must not require Windows DLL dirs on Linux, ffmpeg executable bits, tkinter picker fallback (paste path) already exists — verify.

## 5. Data location
- `paths.py`: **portable mode** (marker `portable.txt` next to the app, or dev checkout) → `ROOT/data`, `ROOT/models`; **installed mode** (marker `installed.json` written by installer with the chosen data dir, or `SOUND_SYSTEM_DATA_DIR`) → that dir (`data/`, `models/`, `logs/`, `tmp/` inside). Updates overwrite app files only; uninstall keeps the data folder unless the user ticks "also delete my files" (must show the path). Models download on first run through the existing downloader (core ~590 MB, progress, resume, cancel).

## 6. README (EN + AR mirrored) + llms.txt
Header: icon, name, one-line pitch ("Clean audio and video on your own computer" / «نظّف الصوت والفيديو على جهازك»), language link, Download button link, then: "New here? Give this page's link to any AI assistant and ask it to install APP_NAME for you." Sections: Features → Install (per OS, the exact commands) → **For AI assistants** → Using it → Privacy & where your files live → Troubleshooting → Updating / uninstalling → Build from source.
**For AI assistants** (exact steps): 1) explain from Features; 2) ask: which OS? On Windows: NVIDIA card? which drive has ≥ 10 GB free?; 3) give the matching command / link (Windows installer by default, one-liner as alternative, macOS/Linux beta one-liner), mention SmartScreen ("More info → Run anyway") / Gatekeeper, the one-time ~590 MB model download and ~2 GB (CPU) / ~6 GB (GPU) runtime install size (measure and fill in); 4) how to use (preset → Process → Save); 5) where files live; 6) troubleshooting table (port busy, no GPU → CPU, VC++ runtime, low disk, antivirus). `llms.txt` at repo root links to those sections (short).
- A test checks README EN/AR + llms.txt list the same commands, version and asset names as the release scripts.

## 7. Release pipeline (files only, not run)
`.github/workflows/ci.yml` (pytest not-slow on windows; ubuntu + macos CPU smoke) and `release.yml` on `v*` tags: verify tag == pyproject version, build frontend, compile launcher, build installer + install.ps1/install.sh + source zip, `SHA256SUMS.txt`, optional signing if a secret exists, LICENSE + THIRD_PARTY_NOTICES (FFmpeg GPL build: notice + source link; model licenses), CPU smoke test of the installed app, **draft** release. Pin action versions by SHA.

## 8. Acceptance tests
`test_launcher_module` (single instance, port busy, Arabic+space root, browser open mocked), `test_exe_launcher_lifecycle` (compile stub, start, second instance exits 0, close → no orphan python/ffmpeg), `test_paths_installed_vs_portable`, `test_variant_extras_lock` (lock resolves cpu/cu124 on win, cpu on linux/mac markers), `test_inno_silent_install_upgrade_uninstall` (silent install into `data\tmp\inst test\تجربة`, CPU variant, health + short Podcast job, reinstall keeps data, uninstall keeps data), `test_install_ps1_dry_run`, `test_install_sh_lint` (shellcheck if available, else bash -n) + dry run under Git Bash, `test_cross_platform_reveal`, `test_readme_llms_sync`. Real CPU install end-to-end once on this machine (needs internet; uv cache in data\tmp).

## 9. Full install catalogue — user decision 2026-11-16 (SUPERSEDES the "one artifact" idea in §1 and the list in §4)
User: "No i wanna the all ways to install not one". Follow-up round: `docs/brainstorm/{astra,argon}_dist2.md` (prompt `dist2_prompt.md`). Every method below ships; the README Install section lists them all per OS with size, internet need and signing warning (EN + AR). The small online installer from §1 stays as the **recommended** default, not the only route.

### Wave 1 — M10c (this milestone)
Windows
1. Online `Safa-<ver>-win-x64-setup.exe` (~100–150 MB; runtime via uv from official indexes, NVIDIA auto-detect, CPU override, data-folder page).
2. Offline CPU `Safa-<ver>-win-x64-cpu-offline-setup.exe` (one file < 2 GiB, runtime + core models included).
3. Offline GPU `Safa-<ver>-win-x64-gpu-offline-setup.exe` + `-1.bin … -N.bin` via **Inno disk spanning** (`DiskSpanning=yes`, slices ≤ 1.8 GiB); setup refuses missing/mixed-version slices with a bilingual message.
4. Portable zips: CPU `…-cpu-portable.zip`; GPU split `…-gpu-portable.7z.001…` (≤ 1.8 GiB) + bilingual `assemble-gpu.bat` (verifies each part against SHA256SUMS.txt before joining, refuses corrupt/missing parts, checks free space, extracts with a bundled 7zr.exe).
5. One-liner `irm …/install.ps1 | iex` (+ documented "download, inspect, run" alternative; `-Variant cpu|gpu -DataDir -Offline`).
6. winget manifest (`packaging/winget/`, wraps the online installer; `winget install --manifest` works locally; submission to winget-pkgs only after the user approves publishing).
7. Scoop manifest (`packaging/scoop/safa.json`, CPU portable zip + hash; GPU via `-Variant` note).
8. From source with uv.
macOS (Apple Silicon first; beta until tested on a real Mac)
9. `install.sh` one-liner (CPU, `~/Applications/Safa`, Desktop `.command`; never strips quarantine from anything but our own files).
10. Homebrew tap formula `packaging/homebrew/safa.rb` (`brew install --formula …`).
11. Portable tarball `Safa-<ver>-macos-arm64.tar.gz`.
12. From source.
Linux x86_64 (beta until tested on real hardware)
13. `install.sh` one-liner (`--variant cpu|cuda`, `~/.local/share/safa`, `~/.local/bin/safa`, `.desktop`).
14. AUR `packaging/aur/PKGBUILD`.
15. Portable tarballs CPU (+ CUDA split if > 2 GiB).
16. From source.
Any OS
17. Docker images CPU + CUDA (multi-stage: node builds dist → python:3.11-slim + ffmpeg + `uv sync --frozen --extra cpu|cu124`), `docker-compose.yml`. Host binding ALWAYS `127.0.0.1:8790:8790`; new `SOUND_SYSTEM_BIND_HOST` (default 127.0.0.1, `0.0.0.0` only set inside the image); Host/Origin guard unchanged; volume `/data` (`SOUND_SYSTEM_DATA_DIR=/data`).
18. **Offline model pack** `Safa-models-core-<ver>.zip` (~590 MB, checksummed) + `Safa-models-all-<ver>` (split if needed) and an in-app / CLI import (`python -m backend.launcher models import <zip>`): offline installers and USB users never need internet.

### Wave 2 — M10d (next milestone, after M10c is reviewed)
`.deb` (`/opt/safa`, menu entry, `Depends: ffmpeg`), unsigned macOS `.app` in `.dmg` (Right-click → Open documented; no automatic quarantine removal), AppImage, PyPI **bootstrap wheel** `safa-audio` (name currently unclaimed; command `safa`; wheel carries app + frontend/dist + pinned uv + locks; `safa setup --device cpu|cuda` provisions the runtime — extras alone cannot select the PyTorch index), Chocolatey package, Flatpak (attempt; if the sandbox blocks GPU/file access, document why and keep it disabled), RPM. Each verified in CI where a runner exists.

### Added acceptance tests (Wave 1)
`test_gpu_split_assembler_and_tamper` (corrupt / missing / mixed-version parts rejected), `test_inno_offline_cpu_lifecycle` and spanned-GPU slice check (build with a small dummy payload in tests; real build once), `test_models_pack_import` (checksum, resume-safe, Arabic path), `test_manifests_and_docker_contract` (winget/scoop/homebrew/AUR/compose versions, hashes, `127.0.0.1:8790:8790` in sync with pyproject), `test_bind_host_default_loopback` (native never binds 0.0.0.0; LAN Host header rejected), `test_readme_lists_every_method` (EN + AR list the same methods and asset names as the release script).
