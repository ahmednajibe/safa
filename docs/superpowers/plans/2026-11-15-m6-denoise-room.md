# M6 — Noise Reduction + Room Echo Implementation Plan

> **For agentic workers:** Implement task-by-task, test-first. Do NOT commit. Read `AGENTS.md`, `docs/SPEC.md` (§8–§11), `docs/FEASIBILITY.md` (DeepFilterNet workarounds!), `docs/brainstorm/room_findings.md`, and the orchestrator's working experiment code `.orchestrator/analysis/try_wpe.py` (DFN call, numpy WPE, auto-resonance EQ, blind metrics) first. M1–M5 are done.

**Goal:** `denoise` ("Reduce background noise": DeepFilterNet3 AI + ffmpeg afftdn classic) and `dereverb` ("Reduce room echo": automatic room-resonance cuts + light WPE) work through jobs, bounded in RAM/VRAM, GPU/CPU per SPEC §9; the Podcast preset now produces the version the user approved by ear ("it's very good now" = DFN 24 dB → WPE → auto-resonance EQ on Recording (3)).

**Spec:** `docs/SPEC.md`; user-approved reference output: `data/review/experiments/rec3_F_denoise_wpe_autoeq_leveled.wav`.

## Global Constraints
- DFN works at **48 kHz only**: assert the op input is 48 kHz (all intermediates are) — never feed other rates (an orchestrator experiment fed 44.1 kHz by mistake).
- DFN model loaded once per worker process; cache dir patched to `models/deepfilternet` exactly like `scripts/feasibility.py`; the torchaudio shim only if import fails.
- Chunked: DFN on ≤ 30 s chunks with 1 s overlap + raised-cosine crossfade; WPE on ≤ 20 s chunks with 2 s overlap; RAM bounded for a 60-min file.
- Each channel processed separately (stereo stays stereo).
- Device per SPEC §9 (`resolve_device`; denoise ai may use GPU; classic and dereverb are CPU). GPU OOM → free → retry the stage on CPU with `warning_code="gpu_fallback"`; after the op, free VRAM (`del model` is NOT required between chunks, only at op end; `torch.cuda.empty_cache()`).
- All user-facing summaries use `msg()` codes (SPEC §11) — add `noise_reduced{db}`, `room_resonances_cut{freqs}`.

## Review Focus
1. Stereo 30-min file through denoise(ai) on GPU and on CPU: completes, peak RSS < 2 GB, peak VRAM delta < 1 GB, output length exact (slow test; report numbers).
2. Chunk seams inaudible: on a 70 s sine+speech file, max sample jump at chunk boundaries ≤ 1.5× the max jump elsewhere (test).
3. OOM fallback: monkeypatch the DFN call to raise `torch.cuda.OutOfMemoryError` on the first GPU chunk → stage completes on CPU, stage record `actual_device="cpu"`, `fallback_reason` set, warning code present (test).
4. A recording with no clear resonances (white-ish noise / synthetic speech) → dereverb applies 0 cuts and says so (`room_resonances_cut{freqs: []}`), output ≈ input when WPE is off (test).
5. Op summaries survive the job: after completion, `GET /api/jobs/{id}` shows each stage's `message_code`/`message_params`/`warning_code` (Task 0) — test with silence + loudness.

---

### Task 0: Persist per-stage results (M5 review fix)
**Files:** `backend/jobs/store.py`, `backend/pipeline/runner.py`, `backend/worker.py`, `backend/api/jobs.py`, tests.
- When an op finishes, write into that stage's entry in `stages_json`: `{"state":"done","actual_device","fallback_reason","message_code","message_params","warning_code","warning_params","seconds"}` (seconds = wall time). `GET /api/jobs/{id}` and every SSE `state` event include `stages`. This makes "Removed 2.9 s of silence in 3 places" visible after the job, deterministic (fixes the SSE race noted in M5).
- Tests: Review Focus 5.

### Task 1: DeepFilterNet engine + `denoise` op
**Files:** Create `backend/media/dfn.py`, `backend/operations/impl/denoise.py`; register; tests `tests/test_op_denoise.py`.
**Interfaces — Produces:**
- `dfn.py`: `class DfnEngine` with `load(device: Literal["gpu","cpu"]) -> None`, `process(x: np.ndarray (T,), atten_lim_db: float) -> np.ndarray`, `close()`; module-level `get_engine(device)` cache; `process_file(in_wav, out_wav, atten_lim_db, device, report, should_cancel) -> Literal["gpu","cpu"]` (actual device; does the chunking/crossfade/per-channel/OOM-fallback).
- `denoise` op: `engine` ai → `process_file(atten_lim_db=strength_db)`; classic → ffmpeg `afftdn=nr=<min(strength_db,40)>:nf=-50:tn=1` via `run_filter`. Summary `msg("noise_reduced", db=<measured non-speech level drop, rounded>)` (measure with silero VAD non-speech RMS before/after, like `.orchestrator/analysis/nonspeech.py`).
- Registry: denoise `strength_db` default **24** (user-approved), range 6–40 (+ option to reach 100 = "Maximum" as advanced? keep 6–40).
- Tests: `speech_noise.wav` noise-only head reduced ≥ 10 dB (ai) and ≥ 6 dB (classic); real Recording (3) non-speech level reduced ≥ 2.5 dB at matched loudness and VAD speech kept ≥ 95 %; Review Focus 1–3; cpu job never touches CUDA (assert `torch.cuda.is_initialized()` false in worker subprocess for cpu jobs — or via env check).

### Task 2: Room echo — `dereverb` op
**Files:** Create `backend/media/room.py`, `backend/operations/impl/dereverb.py`; register; modify registry (no longer `planned`); tests `tests/test_op_dereverb.py`, `tests/audio_metrics.py` (add `srmr_proxy`, `ltas_ripple_db` from try_wpe.py).
**Interfaces — Produces:**
- `room.py`: `find_room_resonances(wav: Path, fmin=220.0, fmax=1000.0, max_cuts=3, min_prominence_db=2.5) -> list[tuple[float, float]]` (Hz, prominence) — Welch nperseg 4096 on **voiced/loud frames only** (frames above the 60th energy percentile), median-filter baseline (15 bins), `find_peaks`; `wpe(x, taps=10, delay=3, iters=3, nfft=1024, hop=256) -> np.ndarray` (port of try_wpe.py, complex64, chunked by the caller); `apply_cuts(x, sr, cuts, max_cut_db, q=4.0)` via pedalboard PeakFilter.
- `dereverb` op params: `strength` select gentle | normal | strong (labels "Gentle", "Normal", "Strong") → max cut 3 / 4.5 / 6 dB; WPE off / on (taps 10) / on (taps 24). Order: **after denoise** — set `dereverb.order = 42` (denoise 40, enhance 50); update SPEC §2 table.
- Summary `msg("room_resonances_cut", freqs=[621, 516, 281])`.
- Tests: Review Focus 4; Recording (3) (skip if missing): LTAS ripple (220–1000 Hz) reduced ≥ 15 % and SRMR proxy not lower than input − 0.5 dB, cuts found within 250–700 Hz; length exact; chunk seams (Review Focus 2 style) for WPE.

### Task 3: Presets
- podcast: highpass, dehum, declick, **denoise(ai, 24)**, **dereverb(gentle)**, enhance(voice), silence(remove), loudness(podcast). echoey_room: denoise(24), dereverb(strong), enhance, loudness. lecture = podcast + subtitles. jump_cut = declick, denoise(24), dereverb(gentle), enhance, silence(remove), loudness(youtube).
- Tests updated.

### Task 4: Review files for the user
- Extend `scripts/make_review_files.py` (NOT run by pytest): for each real recording run the **Podcast** preset through the real pipeline → `data/review/m6_<stem>_podcast.wav`; also `m6_<stem>_podcast_keep_silence.wav` (same minus silence). Report per recording: input/output duration, non-speech level drop, SRMR, ripple, cuts found, LUFS, TP, device used, seconds per stage.
- [ ] `uv run pytest -m "not slow"` → all pass; run the slow tests once and report.

## Final report
Files, commands + outcomes, the per-recording table, VRAM/RSS numbers, deviations, open problems.
