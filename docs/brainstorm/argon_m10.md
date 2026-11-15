Using `brainstorming` and `verification-before-completion` for the M10 design review.

### 1. Must-Have vs. Nice-to-Have & What’s Missing
* **Must-Haves (Ship-Blockers):**
  * **#1 `start.bat` staleness & preflight:** Auto-rebuild `frontend/dist` and run `uv sync` when sources/locks are newer; bilingual (EN/AR) errors for busy port, missing tools, or low disk.
  * **#2 First-run `prefetch_models.py` fix:** **Bug found:** [prefetch_models.py](file:///F:/sound_system/scripts/prefetch_models.py#L77-L92) currently skips `deepfilternet3` (`"prefetch not implemented yet"`), which breaks offline "Podcast cleanup". Must prefetch `deepfilternet3`, `htdemucs`, and `whisper_small` in setup.
  * **#3 Offline verification:** End-to-end test running Podcast + Separation + Subtitles with external sockets blocked.
  * **#4 Retention cleanup:** Startup + daily sweep deleting expired terminal jobs and unreferenced `data/media/<id>` while preserving external `saved_path` copies.
  * **#5 Pre-job disk check & temp redirection:** [media.py](file:///F:/sound_system/backend/api/media.py#L261-L265) checks upload space, but [jobs.py](file:///F:/sound_system/backend/api/jobs.py#L118-L211) (`POST /api/jobs` and `/retry`) has **no disk check**. A 60-min MP3 (~50 MB) expands to ~1.4 GB per 48 kHz float32 stereo WAV intermediate (`×2` during stage handoff in [runner.py](file:///F:/sound_system/backend/pipeline/runner.py#L220-L247)). Estimate from `duration_s` before queuing and redirect `TEMP`/`TMP` to `data/tmp`.
  * **#7 Bilingual README (EN/AR) & #9 Crash recovery:** Keep README focused on 1-click setup, offline privacy, and troubleshooting. Orphans/interrupted jobs are mostly handled in [main.py](file:///F:/sound_system/backend/main.py#L130-L143) and [supervisor.py](file:///F:/sound_system/backend/jobs/supervisor.py#L178-L210).
* **Nice-to-Have (Keep Minimal):**
  * **#2 In-UI model downloads:** Only needed for optional heavy models (`whisper_turbo` 1.6 GB, `kim_vocal_2`); core models belong in setup.
  * **#6 Rotating logs + Diagnostics, #8 Shortcut, #10 60-min benchmark, #11 A11y pass:** Keep lightweight (`RotatingFileHandler` 5 MB×3, path-scrubbed diagnostics JSON).
* **Missing from Draft:**
  * **C:-drive cache redirection:** `uv`, `npm`, and `$env:TEMP` in [setup.ps1](file:///F:/sound_system/scripts/setup.ps1#L12-L25) write gigabytes to `C:\Users\...`. Redirect `UV_CACHE_DIR`, `npm_config_cache`, and `TEMP`/`TMP` to `<Root>\data\tmp` so installing on `D:`/`F:` never fills `C:`.
  * **Stale capability cache:** [capabilities.py](file:///F:/sound_system/backend/core/capabilities.py#L117-L124) uses `@cache`; `refresh_capabilities()` must be called after any model download.
  * **Security:** Keep binding `127.0.0.1` only (never LAN `0.0.0.0`—no auth, and [settings.py](file:///F:/sound_system/backend/api/settings.py#L63-L107) opens native Explorer/Tkinter dialogs).

### 2. Concrete Answers to Open Questions
* **Model Downloads:** Two-tier: (1) `setup.ps1` prefetches core models (~670 MB: `deepfilternet3`, `htdemucs`, `whisper_small`) using `.part` atomic writes + SHA256 checks + `huggingface_hub` resume. (2) Optional models (`whisper_turbo` 1.6 GB, `kim_vocal_2` 60 MB) download on-demand in UI or via `setup.ps1 -AllModels`.
* **Retention:** 7 days default (configurable `1 / 7 / 30 / Never` in Settings). Delete `data/jobs/<id>` and orphaned `data/media/<id>`, **never** touching `job_files.saved_path`. Show `"Expires in Xd"` in History.
* **Packaging & CPU-Only Friends:** Ship a **portable zip** with pre-built `frontend/dist` (eliminating Node.js on user laptops), standalone `uv.exe`, and `tools/ffmpeg`. In `setup.ps1`, auto-detect `nvidia-smi`:
  * **GPU laptops:** `cu124` wheels + core models (~3.5 GB disk).
  * **CPU-only laptops:** Skip 2.5 GB of CUDA/cuDNN wheels; install CPU `torch`/`onnxruntime` (~1.1 GB disk total).

### 3. Risks in `start.bat` / `setup.ps1` / Prefetch Flow
* **Arabic / spaces in paths:** Add `chcp 65001 >nul` and `PYTHONUTF8=1` in [start.bat](file:///F:/sound_system/start.bat#L1-L10) so batch/PowerShell/C++ loaders (`ctranslate2`/`onnxruntime`) don’t mangle non-ASCII paths.
* **Mark-of-the-Web & ExecutionPolicy:** Zip downloads from browsers/WhatsApp get `Zone.Identifier`, blocking scripts/DLLs. Run `Get-ChildItem -Recurse | Unblock-File` in setup.
* **Windows 260-char MAX_PATH:** HF snapshot paths (`models/whisper/models--mobiuslabsgmbh--faster-whisper-large-v3-turbo/snapshots/<sha>/...`) easily exceed 260 chars inside deep user folders; warn if `$Root.Length > 80` or flatten cache paths.
* **OneDrive folders:** Running inside `OneDrive\Desktop` locks SQLite WAL (`app.db-wal`) and multi-GB WAV intermediates (`WinError 32`) and exhausts cloud quota. Detect `"OneDrive"` in `$Root` and warn.
* **Antivirus & slow cold start:** Defender scanning `.venv` DLLs can exceed the 30s timeout in [open-when-ready.ps1](file:///F:/sound_system/scripts/open-when-ready.ps1#L10); raise to 90s.
* **Missing VC++ runtime / old GPU driver:** Missing `vcruntime140_1.dll` crashes `import torch`/`onnxruntime`; old NVIDIA drivers pass `nvidia-smi` in [gpu.py](file:///F:/sound_system/backend/core/gpu.py#L74-L97) but fail CUDA 12.4 runtime—verify `torch.cuda.is_available()` in health/preflight and auto-fallback to CPU.

### 4. M10 Acceptance Tests
1. **`test_job_disk_check`:** `POST /api/jobs` and `/retry` estimate peak bytes from `duration_s` and return `507 insufficient_storage` when free space is low.
2. **`test_retention_cleanup`:** Deletes terminal jobs/unreferenced media older than `retention_days`; preserves `queued`/`running` jobs, shared media, and `saved_path` files.
3. **`test_prefetch_and_offline_pipeline`:** Prefetches `deepfilternet3`, `htdemucs`, `whisper_small`, blocks non-loopback sockets (`HF_HUB_OFFLINE=1` + socket guard), and runs Podcast + Separation + Subtitles end-to-end.
4. **`test_arabic_temp_isolation`:** Runs pipeline with Arabic + space paths and verifies zero temp files leak to `C:\Users\...\Temp`.
5. **`test_diagnostics_scrubbing`:** Verifies diagnostic output redacts `%USERPROFILE%` and personal filenames.
