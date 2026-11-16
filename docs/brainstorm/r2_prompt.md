Final full code review (R2) of F:\sound_system. The product is Safa (صفا): a local web app that cleans audio and video.

**Stack:**
- FastAPI on 127.0.0.1:8790 with SQLite.
- A supervisor plus one worker subprocess per job.
- A DSP/AI pipeline: DeepFilterNet, demucs, silero-vad, faster-whisper, pedalboard, ffmpeg.
- A React + Vite frontend.
- Distribution: Inno installers, portable zips, install.ps1/install.sh, Docker, PyPI bootstrap wheel, deb/rpm/AppImage/dmg/flatpak, and CI workflows.

Milestones M1–M10d are done. This is the last review before the user tests and before any public release.

Load and use the skills that fit: code review, security review, systematic debugging, test-quality thinking.

READ ONLY:
- Do not write, change or delete any file.
- Do not install anything.
- Do not run servers.
- Never touch tests/fixtures/real/ contents or D:\projects\learning.

Read:
- docs/SPEC.md (§8+ override earlier sections) and docs/PLAN.md;
- then backend/** (api, core, jobs, media, operations), frontend/src/**, scripts/**, packaging/**, install.ps1, install.sh, Dockerfile*, .github/workflows/*, and tests/**.

Report real defects only. No style nits. For each finding:
- severity: critical / high / medium / low;
- file:line;
- what goes wrong, with the concrete input or state that triggers it;
- a suggested fix in one or two lines.

Areas to cover:
1. **Security:**
   - Host/Origin guard, CSRF, and path traversal in upload, download, output-dir and reveal.
   - Subprocess argument building.
   - Arbitrary file write and delete: retention, uninstallers, install scripts.
   - Checksum and hash verification.
   - Loopback binding in every install method.
   - Secrets or personal data in logs and diagnostics.
2. **Correctness:**
   - The job lifecycle: cancel, retry, interrupted-on-restart, SSE.
   - Timeline mapping (trim/speed/silence cut, subtitles mapped through the timeline).
   - Operation order and conflicts.
   - Loudness targets.
   - Video/audio sync.
   - Arabic and space-containing paths.
3. **Resources:** VRAM freed after every GPU stage, CPU fallbacks, RAM on 60-minute files, temp files, disk-full handling.
4. **Performance:** the hot spots. The in-speech mouth-click detector (backend/media/mouthclick.py) makes a 60-minute file take about 66 minutes even on GPU. Suggest concrete speedups that do not change output, or only change it negligibly.
5. **Test quality:**
   - Tests that would still pass if the code were wrong.
   - Flaky tests: tests/test_temp_redirect.py::test_no_system_temp_leak snapshots the whole system %TEMP% and fails when other processes write there.
   - Missing coverage on risky paths.
6. **Distribution gaps:**
   - Linux CUDA: the .deb/.rpm/tarball are about 4.9 GB, over the 2 GiB release limit, so `install.sh --variant cuda` points at an asset that cannot be published.
   - Any installer or uninstaller flow that can lose user data.
   - Docs that contradict the code (README.md, README.ar.md, llms.txt).
7. **Frontend:** state bugs, error handling, i18n/RTL gaps, accessibility.

Reply at most about 1500 words. Put the most severe findings first, and end with the top 10 to fix.
