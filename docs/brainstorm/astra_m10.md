I’m using brainstorming for the design choices, verification-before-completion to ground findings in the files, and Impeccable for first-run usability. Context loading did not run; I’ll read the existing project context directly. No security-review plugin skill was found, so I’ll apply security review thinking directly. This review will stay read-only.

The biggest first-run gap is that setup never calls prefetch, and `prefetch_models.py --all` still skips DeepFilterNet while returning success. Also, putting the app on F: doesn’t keep installation writes off C:—ffmpeg uses `%TEMP%`, and package/Python caches aren’t redirected. I’m checking those storage paths and the existing recovery safeguards against the draft’s acceptance criteria.

- **1. Draft priorities — must-have unless marked optional:**
  - **1 Launcher:** reliable freshness checks, dependency validation, bilingual failures; include build configuration/public assets, not just `src`.
  - **2 Models:** complete, recoverable downloads with accurate availability.
  - **3 Offline:** verify actual processing without external connections.
  - **4 Retention:** bounded storage, visible expiry, safe deletion.
  - **5 Disk:** checks before upload/preparation, download and job execution; handle exhaustion mid-operation.
  - **6 Logs:** bounded app/worker logs required; sanitized “Copy diagnostics” optional.
  - **7 README:** English/Arabic quick start, privacy, recovery and uninstall required; currently absent.
  - **8 Shortcut:** optional; defer autostart.
  - **9 Recovery:** required, including console-close and forced termination.
  - **10 Performance:** representative hour-long CPU/GPU runs required; extensive benchmarking optional.
  - **11 Accessibility:** keyboard, labels, contrast, reduced motion and Arabic/RTL required.
  - **Missing:** storage-location selection before installation, repair/retry flow, single-instance protection, documented manual updates. Keep `127.0.0.1`; defer LAN sharing and automatic updates.

- **2. Concrete decisions:**
  - **Downloads:** setup and UI should share one downloader. Offer DFN + Whisper small initially; separation and turbo individually optional. Use allowlisted model IDs, pinned revisions/checksums, resumable partial files, atomic promotion, cancellation and capability refresh. Mirrors must serve identical verified bytes. Show measured download/install requirements rather than promising “~2.5 GB.”
  - **Retention:** seven days from terminal completion is reasonable, with visible expiry and “Keep.” Expire app-owned outputs even when exported; never touch external copies. Preserve shared media until every retained reference expires; protect active preparation, queued/running jobs and downloads. Do not infer successful export from a download click.
  - **Packaging:** ship a versioned portable Windows x64 folder with private Python, built frontend, ffmpeg and launcher; friends should need neither Node nor uv. Provide CPU and optional GPU builds. Current environment + models + ffmpeg totals **≈11.85 GiB** before packaging. CPU size remains unmeasured; provisionally budget 2–4 GiB plus models. Measure compressed size and extraction headroom before release; don’t redistribute the development venv unchanged.

- **3. Current risks:**
  - Setup never calls prefetch; `--all` skips DFN yet succeeds. Prefetch also omits cache-environment initialization. [prefetch_models.py:77-96](file:///F:/sound_system/scripts/prefetch_models.py)
  - ffmpeg extraction uses `%TEMP%`; uv/Python/npm caches can fill C:. Downloads lack application-enforced checksums; missing ffprobe isn’t repaired when ffmpeg exists. [setup.ps1](file:///F:/sound_system/scripts/setup.ps1)
  - Launcher interpolates an unvalidated port into PowerShell. Quoted paths help, but Arabic, spaces, brackets and long paths need Windows coverage. Process-scoped execution-policy bypass cannot override organizational policy.
  - Test SmartScreen/antivirus and Controlled Folder Access failures without recommending disabled protection. Avoid OneDrive for databases, caches and private recordings. Diagnose VC++ runtime and driver failures separately; CPU must work without NVIDIA.
  - Remove production `testserver` Host acceptance and enforce exact origins. [main.py:79-92](file:///F:/sound_system/backend/main.py)

- **4. Acceptance tests:** fresh standard-user installs; nearly-full C: with another drive selected; Arabic/space/long paths; missing prerequisites; stale builds; duplicate launches/ports; interrupted/corrupt downloads; externally blocked networking across every advertised engine; disk exhaustion; retention races/junction escapes; crash/retry with no orphan processes; CPU-only and GPU hour-long runs; hostile Host/Origin requests; diagnostic redaction; EN/AR keyboard and screen-reader walkthroughs.

Read-only inspection and size measurement only; no files changed or tests run.
