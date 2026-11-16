Short follow-up design round for F:\sound_system milestone M10c (product name Safa / صفا). Read docs/superpowers/plans/2026-11-16-m10c-distribution.md (current plan), docs/brainstorm/astra_dist.md and argon_dist.md, pyproject.toml, backend/core/paths.py.

The user rejected "one main install route" and said (verbatim): "No i wanna the all ways to install not one". So Safa must offer EVERY reasonable install method on every OS. Measured sizes: CPU portable zip 560 MB (2.16 GB extracted), GPU portable zip 4.93 GB (9.25 GB extracted); GitHub release assets must be < 2 GiB each; core models ~590 MB downloaded on first run.

Candidate methods (judge each; add missing ones):
Windows: (a) small online setup.exe (runtime via uv, NVIDIA auto-detect); (b) full OFFLINE setup.exe CPU; (c) full OFFLINE GPU installer/zip split into <2 GiB volumes (7z multi-volume or Inno disk spanning) with an assembler; (d) portable zips CPU + GPU(split); (e) one-line `irm …/install.ps1 | iex`; (f) winget manifest; (g) Scoop bucket manifest; (h) Chocolatey; (i) from source with uv.
macOS: (j) one-line install.sh; (k) Homebrew tap (formula or cask); (l) .dmg/.app (unsigned) ; (m) from source.
Linux: (n) install.sh; (o) AppImage; (p) .deb; (q) Flatpak; (r) AUR.
Any OS: (s) Docker images CPU + CUDA (docker run -p 127.0.0.1:8790:8790 -v data) + docker-compose; (t) Python package on PyPI: `uv tool install safa` / `pipx install safa` (wheel must include frontend/dist; torch variant selection via extras or index); (u) from source.

Load and use the skills that fit (brainstorming, writing-plans thinking, security review thinking). Read only: do NOT write or change files, no installs, never delete anything. Reply max ~500 words, bullets:
1. For each method: keep now / keep later / drop, with one-line reason (effort, size limit, maintenance, signing, real value for non-technical Arabic-speaking friends).
2. For the GPU offline package: the best way to deliver > 2 GiB via GitHub releases.
3. For PyPI/uv tool and Docker: concrete design (package name availability risk, how torch CPU vs CUDA is chosen, where data/models live, port/host binding stays loopback-only, offline after first run).
4. Which of these can be verified on this Windows machine / in CI, and the acceptance tests to add.
