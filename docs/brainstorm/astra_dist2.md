I’m using brainstorming and writing-plans to assess each route and its acceptance tests. I’ll keep this read-only and review download integrity, package sources, and local network exposure; no security-review plugin skill was found.- **Retain all reasonable routes in Safa’s installation catalogue.** “Later” means scheduled support, not removal. Arabic guidance should explain download size, internet requirements, and signing warnings beside each option.

- **Windows**
  - **(a) Online setup.exe — keep now:** bilingual wizard, NVIDIA recommendation, manual CPU override.
  - **(b) Offline CPU setup.exe — keep now:** practical for friends with unreliable internet.
  - **(c) Offline GPU installer — keep now:** split payload solves GitHub’s limit.
  - **(d) CPU/GPU portables — keep now:** USB sharing; existing packaging provides a foundation.
  - **(e) PowerShell one-liner — keep now:** reuse bootstrap; offer download/inspect/run instructions too.
  - **(f) winget — keep now:** wrap the installer; publication requires validation.
  - **(g) Scoop — keep now:** maintain a bucket using portable assets.
  - **(h) Chocolatey — keep later:** additional moderation and maintenance.
  - **(i) uv source — keep now:** reproducible troubleshooting/development route.

- **macOS**
  - **(j) install.sh — keep now:** quickest coverage, initially beta.
  - **(k) Homebrew tap — keep now:** formula first; cask follows app bundle.
  - **(l) Unsigned .app/.dmg — keep later:** useful GUI route; Gatekeeper friction requires testing, not automatic quarantine removal.
  - **(m) Source — keep now:** Apple Silicon first; Intel needs dependency-pin validation.

- **Linux**
  - **(n) install.sh — keep now:** portable installation mechanism.
  - **(o) AppImage — keep later:** attractive GUI distribution; native-library compatibility work.
  - **(p) .deb — keep later:** familiar desktop installation; validate upgrades and dependencies.
  - **(q) Flatpak — keep later:** sandbox, file access, and GPU integration add work.
  - **(r) AUR — keep now:** modest packaging effort for technical Arch users.

- **Any OS**
  - **(s) Docker/Compose — keep now:** reproducible deployment; CUDA requires compatible NVIDIA hosts.
  - **(t) PyPI/uv tool/pipx — keep later:** currently `package=false`, CUDA-specific dependencies, and repository-relative assets.
  - **(u) Source — keep now:** document each supported OS/architecture.
  - **Add:** macOS/Linux portable tarballs now; RPM, Snap, Nix, Conda, macOS `.pkg`, Windows MSI/MSIX later—additional ecosystem coverage and maintenance.

- **GPU delivery:** Prefer Inno disk spanning: setup.exe consumes adjacent **1.8-GiB maximum** payload files directly. Portable alternative: 7z volumes plus bilingual assembler and bundled extractor. Verify authenticated checksums before extraction; reject missing/mixed-version parts; check staging space. Offline kits must include models for advertised features, including approximately 590 MB core models.

- **PyPI design:** `safa` is already occupied; `safa-audio` returned 404, which does not reserve it. Keep executable `safa`. Ship a bootstrap wheel containing frontend assets, app, pinned uv, and runtime locks. `safa setup --device cpu|cuda` provisions Python 3.11/ffmpeg and official CPU/cu124 wheels; macOS uses PyPI. Extras alone cannot select indexes. Install exactly one ONNX Runtime distribution.

- **Storage/network:** Add writable Safa roots under LocalAppData, macOS Application Support, or XDG, with relocation. Docker uses `/data/{data,models}`:
  `docker run -p 127.0.0.1:8790:8790 -v safa-data:/data IMAGE`
  Compose mirrors this. Explicit container-only `0.0.0.0` binding is necessary; native binding stays loopback. Preserve Host/Origin checks. CPU/CUDA images bake matching runtimes. Cached features work offline after downloads finish.

- **Verification:** Windows can verify installers, GPU/fallback, launchers and manifests; native CI verifies macOS/Linux packages and CPU Docker. Add missing/corrupt-volume, Arabic-path, clean-machine, upgrade/data-preservation, packaged-frontend, interrupted-download, offline-processing, and LAN/Host/Origin rejection tests. CUDA needs hardware runners.

- **Read-only:** inspected files/graph and queried PyPI; no changes, installations, or tests performed.
