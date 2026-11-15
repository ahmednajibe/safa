# M5 — Cut Silence Implementation Plan

> **For agentic workers:** Implement task-by-task, test-first. Do NOT commit. Read `AGENTS.md`, `docs/SPEC.md` **§10 (Cut silence = remove silence — the user's explicit meaning)**, §8, `backend/media/timeline.py`, `backend/media/cutter.py`, `backend/operations/registry.py` first. M1–M4b are done.

**Goal:** The `silence` op ("Cut silence") outputs the recording **without its silent parts** (default), or with long pauses shortened (option), for audio and video (jump cut), driven by speech detection (Silero VAD) or a volume threshold, feeding the M4b timeline.

**Architecture:** Detection (`backend/media/silence_detect.py`) returns speech intervals in seconds; a pure planner (`backend/media/silence_plan.py`) turns them into keep-intervals per SPEC §10; the op snaps to the video frame grid, renders with `cutter.render_keep`, and returns `timeline.keep(...)`.

**Tech Stack:** silero-vad 5.1.2 (PyPI, bundled weights, torch CPU), soxr for 48k→16k, ffmpeg `silencedetect`, soundfile.

**Spec:** `docs/SPEC.md` §10

## Global Constraints
- Defaults (SPEC §10): `action="remove"`, `min_silence_s=0.3`, `pad_s=0.1`, `keep_pause_s=0.25` (shorten only), `mode="vad"`, `threshold_db=-40` (amplitude only).
- Remove mode removes leading and trailing silence too.
- VAD runs on a 16 kHz mono copy; timestamps applied to the 48 kHz signal. Process long files in windows (≤ 10 min with 2 s overlap, merge intervals) so RAM stays bounded.
- Silero runs on CPU always (it is tiny); the op reports `actual_device="cpu"`.
- No speech found → output = input unchanged, `message="No speech found, so nothing was cut. Try Volume threshold mode."` (never an empty file).
- Report a summary via `msg("silence_removed", seconds=12.4, count=37)` / `msg("pauses_shortened", ...)` / `msg("no_speech_found")`: `"Removed 12.4 s of silence in 37 places"` / `"Shortened 9 pauses, saved 6.1 s"`.

## Review Focus
1. A 0.2 s gap between two words is kept when `min_silence_s=0.3` (no word-crashing) (test on synthetic SAPI speech: concatenate two phrases with a 0.2 s gap and a 1.0 s gap; only the 1.0 s one is cut).
2. Speech is not clipped: VAD speech total of the output ≥ 95 % of the input's VAD speech total on each real recording (test, skip if missing).
3. Silence-only file → unchanged + message (test).
4. Video jump cut: an mp4 built from `speech_silences.wav` + testsrc → output video and audio durations equal (±1 frame) and equal to the timeline's `out_duration`; `/api/jobs/{id}/timeline` lists the removed spans (test).
5. 30-minute generated file (speech fixture repeated with gaps) completes with peak RSS of the worker < 1.5 GB (measure with psutil in the test; mark `@pytest.mark.slow`, still run it once and report the number).

---

### Task 0: Localizable backend messages (SPEC §11 "Message codes") — do first
**Files:** Create `backend/core/messages.py`; modify `backend/operations/base.py` (`OpResult.message_code/message_params`, `warning_code/warning_params`), `backend/pipeline/types.py` (`Progress.message_code/params`), `backend/jobs/store.py` + `backend/db.py` (persist `message_code`, `message_params_json`, `error_code`, `error_params_json`, `warnings_json` — idempotent migration), `backend/api/errors.py` (`err(status, code, message, params=None)` → `{"code","message","params"}`), `backend/api/estimate.py` (`bucket_code` + `minutes`), `backend/api/operations.py` (`unavailable_code`, conflict `{conflict_code, blocker_op_id}`), every existing op that sets a message (loudness `very_quiet_raised`, fallbacks `gpu_fallback{op_id}`), worker/supervisor (`job_interrupted`, `job_cancelled`, `job_failed{op_id}`), media API errors.
- `messages.py`: `MESSAGES: dict[str, str]` English templates keyed by code (`"silence_removed": "Removed {seconds} s of silence in {count} places"`, …); `msg(code, **params) -> tuple[str, str, dict]` returning (english_text, code, params); unknown code raises in tests.
- Tests: every code used in the codebase exists in `MESSAGES` (grep-style test over `backend/`), SSE state carries `message_code`, API errors carry `params`, estimate returns `bucket_code`, migration idempotent.

### Task 1: Registry params + presets
**Files:** Modify `backend/operations/registry.py`, `tests/test_operations.py`, `frontend/src` only if op names are hard-coded there (they should come from the API).
- `silence` OpSpec: name "Cut silence", description from `docs/UI_DESIGN.md` §7, params exactly per SPEC §10 (keys, defaults, ranges, labels, advanced flags). Remove the old `max_pause_s` param.
- Presets podcast / lecture / jump_cut use `{"action": "remove"}`.
- Conflict rule from M2 (karaoke disables silence vad) still holds; amplitude mode stays allowed.
- Tests updated: params/defaults, presets.

### Task 2: Detection
**Files:** Create `backend/media/silence_detect.py`; tests `tests/test_silence_detect.py`.

**Interfaces — Produces:**
- `speech_intervals_vad(wav: Path, threshold: float = 0.5, min_speech_ms: int = 150, report=None, should_cancel=None) -> list[tuple[float, float]]` — soxr resample to 16 kHz mono per window; `silero_vad.load_silero_vad()` + `get_speech_timestamps(..., sampling_rate=16000, return_seconds=True, min_silence_duration_ms=100)`; windows merged; sorted, non-overlapping.
- `speech_intervals_amplitude(wav: Path, threshold_db: float, min_silence_s: float, report=None, should_cancel=None) -> list[tuple[float, float]]` — ffmpeg `silencedetect=n=<threshold_db>dB:d=<min_silence_s>` via `run_ffmpeg` to the null muxer; parse `silence_start`/`silence_end` (handle a trailing silence with no end); complement within [0, duration].
- Tests: on `speech_silences.wav`, VAD gaps overlap the recorded silence intervals within ±0.3 s (as in M1 feasibility); amplitude mode finds the same gaps on a tone-burst file; window merge on a file longer than one window (use a small window size param in the test).

### Task 3: Planner
**Files:** Create `backend/media/silence_plan.py`; tests `tests/test_silence_plan.py`.

**Interfaces — Produces:** `plan_keep(speech: list[tuple[float, float]], duration: float, action: Literal["remove","shorten"], min_silence_s: float, pad_s: float, keep_pause_s: float) -> list[tuple[float, float]]`.
- Algorithm: pad every speech interval by `pad_s` (clamped to [0, duration]) and merge overlaps → `blocks`. For each gap between consecutive blocks: if gap < `min_silence_s` → keep the gap (merge the blocks). Else remove: drop the gap entirely; shorten: keep `keep_pause_s` of it (half at the end of the left block, half at the start of the right). Remove mode also drops everything before the first block and after the last; shorten mode keeps at most `keep_pause_s` of leading/trailing silence. Empty `speech` → `[(0, duration)]` (the op turns this into the no-speech message).
- Tests (pure, no audio): Review Focus 1 logic, padding clamps at 0/duration, shorten math, leading/trailing handling, empty input, result sorted/non-overlapping and within [0, duration].

### Task 4: The op
**Files:** Create `backend/operations/impl/silence.py`; register in `impl/__init__.py`; tests `tests/test_op_silence.py`.

**Interfaces — Produces:** `run(in_wav, params, ctx) -> OpResult`: detect (by mode) → `plan_keep` → snap to `ctx.fps` when video (use `Timeline`'s snapping helper or a shared `snap_intervals(intervals, fps)`) → if keep == whole file → copy input, message → else `render_keep(in_wav, out, keep)` and `OpResult(timeline=ctx.timeline.keep(keep), message=summary)`. Progress: detection 0–60 %, render 60–100 %. Cancel honoured in both.
- Tests: Review Focus 2 (real recordings), 3, remove vs shorten durations on `speech_silences.wav` (expected durations computed from `fixtures.json` intervals ± 0.15 s), summary message format.

### Task 5: End-to-end
- Review Focus 4 through the jobs API with `{"silence": {}}` (defaults = remove), and a real recording with the "Podcast cleanup" preset minus not-yet-implemented ops (denoise) → completed, output shorter than input, loudness on target.
- Review Focus 5 (slow).
- [ ] `uv run pytest -v` → all pass.
- [ ] Leave for the orchestrator/user to listen: `data/review/m5_<recording stem>_cut.wav` for each of the three real recordings (silence removed, defaults), and `data/review/m5_jumpcut.mp4`. Report for each: input duration, output duration, seconds removed, number of cuts.

## Final report
Files, commands + outcomes, the per-recording numbers, peak RSS, deviations, open problems.
