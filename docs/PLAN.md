# Implementation Plan

Each milestone is handed to the coding agent (Devin SWE-2 Max) one at a time. The orchestrator reviews, runs the "done" checks, and sends back issues until it passes. Spec: `docs/SPEC.md`.

## M1 — Environment & feasibility gate
- `pyproject.toml` (uv, Python 3.11, `.python-version`), PyTorch CUDA index configured, deps pinned in `uv.lock`.
- `scripts/setup.ps1`: `uv sync`, download pinned ffmpeg+ffprobe Windows static build into `tools/ffmpeg/` (skip if present), `npm ci` + build frontend when it exists.
- `scripts/make_fixtures.py`: generates `tests/fixtures/` (speech-like + noise + music-like tone bed, silences; a 10 s mp4 with that audio; a file with an Arabic + spaces name).
- `scripts/feasibility.py`: for torch CUDA, DeepFilterNet3, audio-separator (htdemucs), silero-vad, pedalboard, faster-whisper: run on a 10 s fixture on GPU and CPU, record OK/FAIL, time, peak VRAM.
- `docs/FEASIBILITY.md`: results table, exact working pins, workarounds (e.g. DeepFilterNet ↔ torchaudio shim).
- **Done:** `uv run python scripts/feasibility.py` exits 0 with every component OK on at least CPU; FEASIBILITY.md filled from a real run.

## M2 — App shell
- `backend/` FastAPI app (`backend/main.py`), settings, SQLite init, `GET /api/health`, `GET /api/operations` (registry skeleton with all ops from SPEC, availability from feasibility checks).
- `frontend/` Vite + React + TS + Tailwind scaffold, dev proxy to backend; FastAPI serves `frontend/dist`.
- `start.bat` → runs setup if needed, starts uvicorn on 127.0.0.1:8765, opens browser.
- pytest + httpx test client set up.
- **Done:** `start.bat` opens a page that shows health info; `uv run pytest` green.

## M3 — Media, jobs, worker, SSE
- Upload, ffprobe probe, audio stream selection, waveform peaks.
- Job model in SQLite, supervisor thread, one worker subprocess per job, progress writes, SSE endpoint, cancel (kill tree), retry, startup → interrupted.
- Pipeline runner skeleton: decode → ops (none yet) → encode (wav/flac/mp3/aac) → video remux with `-c:v copy`.
- Downloads endpoint.
- **Done:** pytest: upload 10 s mp4 → job with no ops → SSE reaches completed → output mp4 has video stream copied + audio; cancel test; Arabic filename test.

## M4 — ffmpeg / DSP operations
- `trim`, `channel`, `highpass`, `dehum`, `declick`, `enhance` (pedalboard chain), `speed`, `loudness` (2-pass loudnorm), presets, conflict rules, effective order.
- Timeline-changing ops produce a keep-interval map; video path re-encodes with h264_nvenc → libx264 fallback.
- **Done:** pytest per op; loudness output within ±0.5 LU of target and true peak ≤ −1 dBTP; trimmed video duration matches audio within 50 ms.

## M5 — Silence cutting
- silero-vad speech mode + silencedetect amplitude mode, pause shortening, padding, 15 ms crossfades, keep-interval list shared with video.
- **Done:** fixture with known silences → output duration within tolerance; quiet speech segments kept; video/audio durations match.

## M6 — Noise reduction
- DeepFilterNet3 adapter (48 kHz, chunked, atten_lim_db), afftdn classic mode, CPU fallback.
- **Done:** noisy fixture SNR/noise-floor measurably reduced; 30 min synthetic file completes without RAM blow-up.

## M7 — Music separation & dereverb
- audio-separator adapter: vocals / instrumental / both stems; htdemucs default, MDX option; dereverb op if feasible; OOM → CPU retry; model download progress.
- **Done:** speech+music fixture → vocals stem energy of music band reduced; peak VRAM < 3 GB; CPU path works.

## M8 — Subtitles & preview
- faster-whisper SRT/VTT on final audio; preview job for region (15–30 s).
- **Done:** SRT produced with timestamps inside audio duration; preview job completes and returns audio.

## M9 — Full UI
- Media inspector, presets, op cards in effective order with params and conflict hints, waveform with preview region, stage stepper + cancel, A/B player (Tab hotkey, keeps playhead) incl. video, downloads, history.
- **Done:** browser walkthrough: upload video → preview → full run → A/B → download mp4.

## M10 — Hardening & packaging
- Retention cleanup, disk-space check, `scripts/prefetch_models.py`, offline check, README (setup, usage, troubleshooting).
- **Done:** fresh clone → `start.bat` works; all features work with network disabled after prefetch.
