# M8 — Subtitles (Transcription) & Preview Implementation Plan (reviewed by Argon; Astra out of quota — `docs/brainstorm/argon_m8.md`)

> **For agentic workers:** Implement task-by-task, test-first. Do NOT commit. Read `AGENTS.md`, `docs/SPEC.md` (§2, §8, §9, §11), `docs/FEASIBILITY.md`, `docs/brainstorm/argon_m8.md` (source-level findings — follow them), `backend/pipeline/runner.py`, `backend/media/timeline.py`, `backend/operations/impl/trim.py` first. M1–M7 are done.

**Goal:** `subtitles` ("Create subtitles" / "تفريغ النص") writes timed SRT/VTT of the final audio with first-class Arabic (Egyptian + MSA) and English; preview jobs work for every op.

## Global Constraints
- Models (exact faster-whisper names): `small` ("Fast", default) and `large-v3-turbo` ("Accurate", recommended for Arabic). No `medium`. Manifest entries `whisper_small`, `whisper_turbo`; capabilities check the specific HF cache folders `models--Systran--faster-whisper-small` and `models--mobiuslabsgmbh--faster-whisper-large-v3-turbo`. Prefetch script includes both.
- Compute: GPU `int8_float32` if in `ctranslate2.get_supported_compute_types("cuda")` else `float32`; CPU `int8`, `cpu_threads=4`. CT2 OOM (`RuntimeError` containing "out of memory") → free model → retry on CPU int8 with `gpu_fallback{op_id:"subtitles"}`. **Measure** turbo VRAM on this GPU in a test/script and record it in FEASIBILITY.md (estimate 1.1–1.5 GB, unverified).
- `transcribe(...)` parameters: `vad_filter=True`, `vad_parameters=dict(threshold=0.5, min_silence_duration_ms=500, speech_pad_ms=200)`, `word_timestamps=True`, `hallucination_silence_threshold=2.0`, `condition_on_previous_text=False`, `repetition_penalty=1.1`, `no_repeat_ngram_size=3`, `no_speech_threshold=0.6`, `log_prob_threshold=-1.0`, `compression_ratio_threshold=2.4`, `multilingual=False`, `language_detection_segments=4`, `append_punctuations` = default + `،؛؟»`, `prepend_punctuations` = default + `«`.
- **Empty/silent input guard:** if Silero finds no speech (or audio is digital silence), skip transcribe and write valid empty SRT/VTT, `subtitles_written{count:0}` (faster-whisper raises on 0 speech chunks with language auto).
- Op order 90 (last): timestamps are already output time. Iterate the lazy segments generator: `should_cancel()` each segment, progress = `seg.end / duration`.
- Cue rules: word timestamps → lines ≤ 42 visible chars, ≤ 2 lines, cue 1–7 s, gap ≥ 80 ms, never split a word; lines containing Arabic (`[؀-ۿ]`) start with U+200F RLM (not counted in the 42). SRT written as `utf-8-sig` (BOM), VTT as `utf-8` (no BOM, `WEBVTT` header).
- Outputs: extra outputs kind `subtitle` written to `job_root/output/` as `<stem>.srt` / `<stem>.vtt` (use `OpContext.out_dir`/`display_stem` from M7); the op returns the input audio unchanged.
- Params: `language` auto | ar | en | fr | es | de | tr | ur; `format` srt | vtt | both; `model` fast | accurate; `max_line_chars` 42 (advanced); `max_lines` 2 (advanced). Karaoke (`separate.output=instrumental`) disables subtitles. Estimates gain RTF entries for both models/devices.
- **Hard (burned-in) subtitles: deferred** (breaks `-c:v copy`, needs libass Arabic shaping + fonts).
- Messages: `subtitles_written{count, language}`, `language_detected{language, prob}`.

## Task 0: Preview + trim bug (fix first)
`backend/operations/impl/trim.py` compares full-file `start_s/end_s` with the preview-length `ctx.duration_s` → `ValueError` when previewing a region. Fix: in preview jobs, shift trim bounds by `−preview_start` and clip to `[0, preview_len]`; if the trim range doesn't intersect the preview, the op is a no-op with a message. Test: preview `[20, 40]` with `trim(25, 50)` → output 15 s (25–40).

## Review Focus
1. Silence-only WAV with `language=auto` → no exception, empty valid SRT/VTT, count 0.
2. Segmenter property test (random Arabic + English words with punctuation): `،؛؟` attached to the preceding word, Arabic lines start with RLM, visible length ≤ 42, ≤ 2 lines, duration 1–7 s, gaps ≥ 80 ms, SRT has BOM, VTT has none, monotonic non-overlapping cues.
3. SAPI English fixture → transcript contains "quick", "brown", "fox"; real recordings (skip if missing) → language detected, ≥ 1 cue per 7 s of speech, cue times within the output duration, no cue inside a region removed by `silence` (run silence before subtitles).
4. Preview `[20, 40]` with trim + silence + loudness + subtitles succeeds; outputs = audio + subtitles only; cue times within `[0, preview_out_duration]`.
5. CT2 OOM → CPU retry (monkeypatched) and cancel mid-generator; 30-min file small model on GPU and CPU within RAM/VRAM limits (slow).

## Tasks
0. Preview/trim fix. 1. `backend/media/transcribe.py` (model loading, compute type, OOM ladder, guard, transcribe params). 2. `backend/media/subtitle_format.py` (cue builder, SRT/VTT writers). 3. `subtitles` op + registry/rules/estimates/manifest/capabilities. 4. Review files: subtitles for the 3 real recordings with both models → `data/review/m8_<stem>_{fast,accurate}.srt`, report language, cue count, seconds per device, turbo VRAM.
- [ ] `uv run pytest -m "not slow"` passes; slow tests once.
