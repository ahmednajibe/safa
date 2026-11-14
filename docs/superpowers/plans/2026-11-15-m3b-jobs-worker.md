# M3b — Jobs, Worker & Progress Implementation Plan

> **For agentic workers:** Implement task-by-task, test-first. Do NOT commit. Read `AGENTS.md`, `docs/SPEC.md` (§4, §5, §8, §9), and the M3a code (`backend/media/`, `backend/pipeline/`) first.

**Goal:** Submitting a job runs `run_pipeline()` in a separate worker process, one job at a time, with live progress over SSE, cancel (kills the whole process tree, ffmpeg included), retry, interrupted-on-restart, history and downloads.

**Architecture:** SQLite is the single source of truth. A supervisor thread inside the API process picks the oldest queued job and spawns `python -m backend.worker <job_id>`. The worker writes progress rows; the API's SSE endpoint polls them. Cancel = DB flag + process-tree kill.

**Tech Stack:** FastAPI (StreamingResponse for SSE), sqlite3, subprocess, `psutil` (add via uv) for process-tree kill and tests.

**Spec:** `docs/SPEC.md`

## Global Constraints
- Exactly one worker process at a time.
- Worker env: when job device is `"cpu"` set `CUDA_VISIBLE_DEVICES=""` (SPEC §9 hard ceiling); always set `PYTHONIOENCODING=utf-8`; Windows `creationflags = CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW`.
- Job settings are a snapshot taken at submission (ops resolved + validated, output, device, preview, audio stream). Retry creates a **new** job from the same snapshot.
- Progress writes throttled to ≤ 4/s per job, but state changes are always written immediately.
- Downloads use `Content-Disposition: attachment; filename*=UTF-8''<percent-encoded>` (Arabic names).
- SSE: `text/event-stream`, `Cache-Control: no-cache`; heartbeat comment every 15 s.

## Review Focus
1. Cancel while ffmpeg is encoding a long file → within 5 s no ffmpeg/worker process for that job remains, job state `cancelled`, partial outputs removed (test with a generated 10-min WAV → mp3).
2. Server restarted while a job is running → that job becomes `interrupted` with message "Stopped because the app was closed", next queued job starts (test by calling the startup hook on a DB with a `running` row).
3. Two jobs submitted back-to-back → second stays `queued` until the first finishes; `queue_position` reported (test).
4. Worker crashes (exception inside pipeline) → job `failed`, `error` holds a short human message + the last stderr lines are saved to `data/jobs/<id>/worker.log`; supervisor continues with the next job (test with an op id that raises).
5. Browser reconnects to SSE mid-job → first event is the current full state, then updates continue (test: open, read 1 event, close, reopen).

---

### Task 0: M3a review fixes (do these first, test-first)
**Files:** `backend/api/media.py`, `tests/test_api_media.py`, `docs/SPEC.md` §5 (update the upload line).
1. **True streaming upload.** Starlette's `UploadFile` spools the whole multipart body into a temp file in `%TEMP%` (system drive C:) before the handler runs → double disk use and the disk check comes too late. Change `POST /api/media` to accept the **raw request body** (`Content-Type: application/octet-stream`) with the original filename in the `X-Filename` header (percent-encoded UTF-8, decode with `urllib.parse.unquote`), streamed with `async for chunk in request.stream()` straight into `original.<ext>`. Disk check from `Content-Length` before reading. Keep all existing error codes. Update all upload tests to the new form; add `test_upload_does_not_use_system_temp` (monkeypatch `tempfile.gettempdir`/`SpooledTemporaryFile` or assert no multipart parsing happens) and `test_upload_filename_header_arabic`.
2. **Don't block the event loop:** run `probe()`, disk checks and DB writes in `starlette.concurrency.run_in_threadpool` inside the async handler.
3. **Surface prep failures:** `_prepare` records `{"prep_error": "<short message>"}` into `probe_json` on exception (and logs the traceback to `data/media/<id>/prep.log`); `GET /api/media/{id}` returns `"ready": {..., "error": str|null}` and `/peaks` returns 500 `{"code":"prep_failed","message":"We couldn't read the audio in this file."}` instead of 202 forever. Test with a monkeypatched `decode_to_wav` that raises.

### Task 1: Job store + DB migration
**Files:** Create `backend/jobs/__init__.py`, `backend/jobs/models.py`, `backend/jobs/store.py`; modify `backend/db.py`; tests `tests/test_job_store.py`.

**Interfaces — Produces:**
- `db.py`: `init_db` adds (idempotently, via `PRAGMA table_info` + `ALTER TABLE`) jobs columns `requested_device TEXT`, `audio_stream INT`, `cancel_requested INT DEFAULT 0`, `stages_json TEXT` (list of `{"id","name","device","fallback_reason"}`), `retry_of TEXT`.
- `models.py`: `@dataclass Job(id, media_id, kind, ops: list[dict], output: dict, preview: dict | None, requested_device: str, audio_stream: int, state: str, stage: str | None, stage_idx: int | None, total_stages: int | None, stage_pct: float | None, overall_pct: float | None, eta_sec: float | None, device: str | None, message: str | None, error: str | None, stages: list[dict], created_at: str, started_at: str | None, finished_at: str | None, retry_of: str | None)` with `to_dict()`; `TERMINAL = {"completed","failed","cancelled","interrupted"}`.
- `store.py` (`JobStore(db_path)`): `create(media_id, kind, ops, output, preview, device, audio_stream, retry_of=None) -> Job`; `get(job_id) -> Job | None`; `list(limit=100) -> list[Job]` newest first; `next_queued() -> Job | None` (oldest); `queue_position(job_id) -> int | None`; `mark_running(job_id, pid)`; `update_progress(job_id, p: Progress)` (throttled unless stage changed); `set_stage_device(job_id, stage_id, device, fallback_reason=None)`; `finish(job_id, state, error=None, message=None)`; `request_cancel(job_id)`; `cancel_requested(job_id) -> bool`; `add_file(job_id, f: OutputFile)`; `files(job_id) -> list[dict]`; `mark_interrupted_on_startup() -> int`; `delete(job_id)`.
- Tests: create/get round-trip, next_queued order, queue_position, throttling (10 updates within 100 ms → ≤ 2 writes but final state change written), mark_interrupted_on_startup (Review Focus 2), migration idempotent on a DB created by M2's `init_db`.

### Task 2: Worker process
**Files:** Create `backend/worker.py`; tests `tests/test_worker.py`.

**Interfaces — Produces:** `main(argv: list[str]) -> int` and `if __name__ == "__main__"`. Steps: `configure_model_env()`; if job device is gpu → `setup_cuda_dlls()`; load job + media + probe from DB; `run_pipeline(..., on_progress=store.update_progress-wrapper, should_cancel=lambda: store.cancel_requested(id), preview=…, device=job.requested_device)`; record outputs via `add_file`; `finish(completed, message="Done")`. `CancelledError` → remove `output/` contents, `finish(cancelled)`. Any other exception → `finish(failed, error=<short human message>)`, traceback to `worker.log`. Exit code 0 for completed/cancelled, 1 for failed.
- Tests (run worker in-process via `main([...])` with a tmp DB/settings): pass-through job on a real recording completes and registers 1 audio file; failing op → failed + worker.log exists (Review Focus 4).

### Task 3: Supervisor
**Files:** Create `backend/jobs/supervisor.py`; modify `backend/main.py` (lifespan: `mark_interrupted_on_startup`, start supervisor; on shutdown stop supervisor, kill running worker tree, mark it interrupted); tests `tests/test_supervisor.py`.

**Interfaces — Produces:** `class Supervisor(store: JobStore, settings: Settings)`: `start()`, `stop(timeout=10)`, `wake()` (called after job creation), `cancel(job_id) -> None` (sets flag; if that job's worker is running, waits up to 2 s for a clean exit then kills the tree with `psutil` children(recursive=True) + parent), `worker_env(job: Job) -> dict[str,str]`, `running_job_id: str | None`. Loop: wait on an Event (or 0.5 s), take `next_queued()`, `Popen([sys.executable, "-m", "backend.worker", job_id], cwd=ROOT, env=worker_env(job), stdout/stderr → data/jobs/<id>/worker.log, creationflags)`, `mark_running`, wait, if the job is still non-terminal after exit → `failed` with "The processing step stopped unexpectedly".
- Tests: `test_worker_env_cpu_hides_cuda`, `test_one_at_a_time` (Review Focus 3), `test_cancel_kills_tree` (Review Focus 1, assert via psutil no process whose cmdline contains the job id), `test_shutdown_marks_interrupted`.

### Task 4: Jobs API + SSE + downloads
**Files:** Create `backend/api/jobs.py`; modify `backend/main.py`; tests `tests/test_api_jobs.py`.

**Interfaces — Produces:**
- `POST /api/jobs` body `{"media_id","ops":{id:params},"output":{"audio_format","keep_video"},"device":"gpu"|"cpu","preview":{"start_s","end_s"}|null,"audio_stream":int|null}` → 201 `Job.to_dict() + {"queue_position"}`. Validation: media exists (404), ops resolved with `rules.resolve` (422 with message; disabled ops are dropped and returned in `"disabled"`), at least one op OR the request is a conversion (empty ops allowed — pass-through convert), device gpu while not usable → 422 `gpu_unavailable`, preview length 1–60 s (422). Stores `stages_json` from resolved ops with expected devices from `resolve_device`. Calls `supervisor.wake()`.
- `GET /api/jobs` → `{"jobs":[…with "filename" of media…]}`; `GET /api/jobs/{id}` → job + `files` + `queue_position`.
- `GET /api/jobs/{id}/events` → SSE: immediately `event: state` with full job JSON; then on every change (poll 250 ms) `event: state`; ends with `event: done` after a terminal state; `: ping` every 15 s (Review Focus 5).
- `POST /api/jobs/{id}/cancel` → 202 (409 if terminal). `POST /api/jobs/{id}/retry` → 201 new job (`retry_of` set; only from failed/cancelled/interrupted, else 409). `DELETE /api/jobs/{id}` → 204 (409 if queued/running), removes `data/jobs/<id>`.
- `GET /api/jobs/{id}/files/{name}` → FileResponse with UTF-8 `filename*` (404 unknown name; name must be one of the job's registered files — no path traversal).
- Tests: full flow on a real recording (submit → SSE until done → download → bytes match file), Arabic download header, cancel endpoint, retry endpoint, gpu_unavailable with monkeypatched caps, path traversal `../../app.db` → 404, preview job returns audio only.
- [ ] `uv run pytest -v` → all pass (M1–M3a included).
- [ ] Manual: start server on 8790, upload `tests/fixtures/real/Recording.m4a` with curl, POST a job with `{"loudness":{}}`?? — NO: ops are not implemented until M4a; use empty ops (convert to mp3) and watch SSE with `curl -N`. Report output. Stop server.

## Final report
Files, commands + outcomes, tests, deviations, open problems.
