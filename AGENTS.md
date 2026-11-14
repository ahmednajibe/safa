# Rules for coding agents in this repo

- Project root: `F:\sound_system`. **Never delete, move, or overwrite anything outside this folder.** Inside it, do not delete `docs/` files you did not create.
- Read `docs/SPEC.md` and `docs/PLAN.md` first. Only implement the milestone you were asked for.
- Python: **3.11 via uv** (`uv sync`, `uv run ...`, `uv add ...`). Never use the global Python 3.13 or `pip install` into it.
- Frontend: `frontend/` with npm.
- Always pass subprocess args as lists; never build shell strings from file paths (Arabic / spaces in paths must work).
- Resolve ffmpeg/ffprobe via `backend/core/ffmpeg.py` (tools/ffmpeg/bin first, then PATH).
- Every GPU stage must have a CPU fallback and free VRAM after it finishes.
- Write pytest tests for what you build; run `uv run pytest` and make it pass before finishing.
- Do not commit; the orchestrator reviews and commits.
- Finish with a short report: files changed, commands run, test results, anything not done or uncertain.
- Real test recordings (user's own, 19–33 s speech, m4a stereo 48 kHz) are copied in `tests/fixtures/real/` (gitignored, personal — never commit, never upload anywhere). Use them for realistic tests (VAD, denoise, separation, whisper, end-to-end). Tests that use them must `pytest.skip` if the folder is missing. Never read or write the original OneDrive folder.
