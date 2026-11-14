# M4a — Audio DSP Operations Implementation Plan

> **For agentic workers:** Implement task-by-task, test-first. Do NOT commit. Read `AGENTS.md`, `docs/SPEC.md` (§2, §3, §8, §9), `docs/FEASIBILITY.md`, `backend/operations/registry.py`, `backend/pipeline/runner.py` first. M1–M3b are done.

**Goal:** The first real cleanup steps work end-to-end through jobs: `channel`, `highpass`, `dehum`, `declick`, `enhance`, `loudness` — streaming (bounded RAM), cancellable, with per-stage progress, verified by measurement on synthetic signals and the user's real recordings.

**Architecture:** A small op interface in `backend/operations/base.py`; one module per op in `backend/operations/impl/`; ffmpeg ops go through a progress-aware, cancellable ffmpeg runner; pedalboard ops stream through `pedalboard.io.AudioFile` in 10 s blocks with `reset=False`. The runner looks ops up in `IMPLEMENTATIONS` instead of raising `NotImplementedError` (unimplemented ids still raise).

**Tech Stack:** ffmpeg filters (`highpass`, `bandreject`, `adeclick`, `deesser`, `loudnorm`, `pan`), pedalboard 0.9.16, soundfile, numpy.

**Spec:** `docs/SPEC.md`

## Global Constraints
- Every op: input = 48 kHz float32 WAV, output = 48 kHz float32 WAV in `ctx.work_dir`, same channel count unless the op changes it (`channel`). Length preserved exactly (±1 sample) for all ops in this milestone.
- RAM bounded: never `sf.read()` a whole file inside an op; stream.
- ffmpeg always with `-nostdin -hide_banner -y -progress pipe:1 -nostats`, output `-c:a pcm_f32le -ar 48000`.
- Cancel: `should_cancel()` polled ≥ 4×/s during ffmpeg and between pedalboard blocks; ffmpeg killed (and the partial output deleted) on cancel.
- All ops in this milestone are CPU (`resolve_device` already says so); report `actual_device="cpu"`.
- Param names/ranges exactly as in `backend/operations/registry.py`; if a param needs adding, add it there with a UI_DESIGN-style label.

## Review Focus
1. Loudness on a nearly silent file (−60 LUFS input) → no crazy gain explosion / clipping: output true peak ≤ −1 dBTP and a `message` "Very quiet recording — raised as far as safely possible" when the target can't be reached within +30 dB (test).
2. Mono input through `enhance` + `loudness` stays mono; stereo stays stereo (test).
3. Cancel during a long `loudness` pass → ffmpeg process gone within 1 s, `CancelledError`, no partial file left (test with a generated 10-min WAV).
4. `dehum` at 60 Hz on a 50 Hz hum does NOT remove the 50 Hz hum (sanity: frequency param honoured) and 1 kHz speech-band tone loses < 1 dB (test).
5. Chained run (channel mono → highpass → dehum → declick → enhance → loudness) on `tests/fixtures/real/Recording.m4a` through `/api/jobs` completes, output plays (ffprobe), loudness within target (test, skip if fixture missing).

---

### Task 0: Bug from M3b review (fix first, test-first)
Output files are named `original_clean.<ext>` instead of `<user's file stem>_clean.<ext>`: the worker passes the stored path `data/media/<id>/original.<ext>`, and the runner derives the stem from that path. Reproduced via the API with an upload named `تسجيل (3).m4a` → download name `original_clean.flac`. Fix: pass the media's display filename (DB `media.filename`) into `run_pipeline` (new param `display_name: str`) and derive the stem from it with `sanitize_stem`. Add an API-level regression test: upload with `X-Filename` = percent-encoded `تسجيل (3).m4a`, run an empty-ops job, assert the registered file name is `تسجيل (3)_clean.<ext>` and the download `Content-Disposition` carries it (percent-encoded). Preview jobs: `<stem>_preview.<ext>`.

### Task 1: Op interface + ffmpeg progress runner + runner wiring
**Files:** Create `backend/operations/base.py`, `backend/operations/impl/__init__.py`, `backend/media/ffmpeg_run.py`; modify `backend/pipeline/runner.py`; tests `tests/test_op_base.py`.

**Interfaces — Produces:**
- `base.py`: `@dataclass OpContext(work_dir: Path, device: Literal["gpu","cpu"], duration_s: float, sample_rate: int, channels: int, report: Callable[[float | None, str | None], None], should_cancel: Callable[[], bool], caps: Capabilities | None = None)`; `@dataclass OpResult(path: Path, actual_device: Literal["gpu","cpu"] = "cpu", fallback_reason: str | None = None, message: str | None = None, timeline: object | None = None, extra_outputs: list[OutputFile] = field(default_factory=list))`; `OpFn = Callable[[Path, dict, OpContext], OpResult]`.
- `impl/__init__.py`: `IMPLEMENTATIONS: dict[str, OpFn]` (filled by Tasks 2–4).
- `ffmpeg_run.py`: `run_ffmpeg(args: list[str|Path], duration_s: float | None, on_pct: Callable[[float], None], should_cancel: Callable[[], bool], out_path: Path | None = None) -> str` (returns stderr text) — Popen with `-progress pipe:1`, parses `out_time_us`/`out_time_ms`, calls `on_pct(0..100)`, polls cancel every 0.2 s → kill, delete `out_path`, raise `CancelledError`; non-zero exit → `ProcError` with stderr tail. `run_filter(in_wav: Path, out_wav: Path, afilter: str, ctx: OpContext, extra: list[str] = []) -> str` convenience.
- Runner: for each `(op_id, params)` → `IMPLEMENTATIONS[op_id]` (missing → `NotImplementedError` as before); passes `OpContext`; forwards `report` into `Progress(stage_pct=…)`; records `actual_device`/`fallback_reason`/`message` via the progress callback (M3b store); deletes the previous intermediate after each successful stage.
- Tests: `test_run_ffmpeg_progress_monotonic` (pcts non-decreasing, last ≥ 99), `test_run_ffmpeg_cancel_kills` (Review Focus 3 core), `test_runner_uses_implementations` (fake op registered in a monkeypatched dict).

### Task 2: ffmpeg ops — channel, highpass, dehum, declick
**Files:** Create `backend/operations/impl/channel.py`, `highpass.py`, `dehum.py`, `declick.py`; tests `tests/test_ops_ffmpeg.py`.

**Interfaces — Produces** (each `run(in_wav, params, ctx) -> OpResult`, registered in `IMPLEMENTATIONS`):
- `channel`: keep → copy (no re-process, just return input path copied); mono → `pan=mono|c0=0.5*c0+0.5*c1`; left → `pan=mono|c0=c0`; right → `pan=mono|c0=c1`; mono input with mono/left/right → unchanged.
- `highpass`: `highpass=f=<cutoff_hz>:poles=2`, applied twice (`,` chained) for 24 dB/oct.
- `dehum`: for k in 1..harmonics: `bandreject=f=<base*k>:width_type=q:w=30` chained; skip harmonics above 20 kHz.
- `declick`: `adeclick` with gentle `w=55:o=75:t=4`, normal `t=2`, strong `t=1.5:b=4` (document the mapping).
- Tests (synthetic via numpy+soundfile in tmp): highpass: 30 Hz sine attenuated ≥ 18 dB, 1 kHz within 1 dB; dehum 50 Hz + 150 Hz harmonic each ≥ 20 dB down, 1 kHz within 1 dB; Review Focus 4; declick: 20 injected single-sample clicks → peak count above 0.5 reduced ≥ 80 %; channel modes → expected channel counts, left picks left signal; all ops keep length ±1 sample.

### Task 3: pedalboard op — enhance (+ ffmpeg deesser)
**Files:** Create `backend/operations/impl/enhance.py`; tests `tests/test_op_enhance.py`.

**Interfaces — Produces:** `enhance` streams with `pedalboard.io.AudioFile` in 10 s blocks, `board.process(block, sr, reset=False)`, writing to a temp WAV, then (if `deess`) `deesser=i=0.4:m=0.5:f=0.5` via `run_filter`. Presets (document in module docstring):
- voice: HighpassFilter 80 Hz → PeakFilter 250 Hz −2 dB q 1.0 → PeakFilter 3500 Hz +3 dB q 1.0 → HighShelfFilter 10 kHz +1.5 dB → Compressor threshold −18 dB ratio 3 attack 5 ms release 80 ms → Gain +2 dB.
- bright: like voice with presence +4 dB and shelf +3 dB.
- warm: like voice with PeakFilter 150 Hz +1.5 dB, presence +1.5 dB, shelf 0 dB.
- Handle pedalboard latency: output length must equal input length (pedalboard Compressor/filters have no latency; assert in test).
- Tests: length preserved, finite, mono stays mono (Review Focus 2), 3.5 kHz band energy increases vs 250 Hz on pink noise for "voice", deess=false skips ffmpeg (monkeypatch run_filter not called), progress reported per block.

### Task 4: loudness (2-pass loudnorm)
**Files:** Create `backend/operations/impl/loudness.py`; tests `tests/test_op_loudness.py`.

**Interfaces — Produces:** target podcast → −16 LUFS, youtube → −14, custom → `params["lufs"]`; `true_peak_db` default −1; LRA 11. Pass 1: `loudnorm=I=..:TP=..:LRA=11:print_format=json` to null muxer (`-f null -`), parse the JSON block from stderr. Pass 2: `loudnorm=I=..:TP=..:LRA=11:measured_I=..:measured_TP=..:measured_LRA=..:measured_thresh=..:offset=..:linear=true:print_format=summary`, output `-ar 48000`. If measured_I < target − 30 (Review Focus 1) clamp target to measured_I + 30 and set `message`. Progress: pass 1 = 0–50 %, pass 2 = 50–100 %. Measure result with `backend/media/loudness.integrated_lufs` + a true-peak measurement (`ebur128=peak=true`, add `true_peak_db(wav) -> float` to `backend/media/loudness.py`).
- Tests: real recording → podcast −16 ±0.5 LU and TP ≤ −0.9 dBTP; youtube −14 ±0.5; custom −23; quiet file (Review Focus 1); length preserved ±1 sample (loudnorm linear mode must not change length — assert; if it does, trim/pad and document); cancel (Review Focus 3).

### Task 5: End-to-end through jobs
**Files:** tests `tests/test_e2e_dsp.py`.
- Review Focus 5 via the HTTP API (TestClient + real supervisor or in-process worker as M3b tests do), plus the "Podcast cleanup" preset minus unimplemented ops (denoise, silence) → completes with `disabled` listing nothing unexpected; stage devices all "cpu".
- [ ] `uv run pytest -v` → all pass.
- [ ] Manual listening check is the orchestrator's job: leave the e2e output of `Recording.m4a` at `data/review/m4a_chain.wav` (gitignored) and say so in the report.

## Final report
Files, commands + outcomes, measured numbers (LUFS/TP per target, dB attenuations), deviations, open problems.
