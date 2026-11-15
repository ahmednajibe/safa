Design review round for F:\sound_system before building milestone M10 (hardening + first-run experience). Read docs/superpowers/plans/2026-11-16-m10-hardening.md (draft + open questions), README if any, start.bat, scripts/setup.ps1, scripts/prefetch_models.py, docs/SPEC.md, docs/FEASIBILITY.md, backend/main.py and backend/core/settings.py.

Users: the user and Arabic-speaking friends, non-technical, Windows laptops (often with a small or full C: drive — it happened during development), some without an NVIDIA GPU.

Load and use the skills that fit (brainstorming, verification-before-completion, security review thinking). You may read files; do NOT write or change files, no installs, never delete anything. Reply max ~500 words, bullets:
1. Must-have vs nice-to-have for each draft item, and what is missing.
2. Concrete answers to the open questions (model download strategy, retention, packaging for non-technical friends incl. size and CPU-only machines).
3. Risks in the current start.bat / setup.ps1 / prefetch flow (paths with Arabic or spaces, PowerShell execution policy, long paths, antivirus, OneDrive-synced folders, missing VC++ runtime / CUDA drivers).
4. The acceptance tests M10 should have.
