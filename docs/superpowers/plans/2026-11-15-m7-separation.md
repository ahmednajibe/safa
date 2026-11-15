# M7 — Separate Voice and Music Implementation Plan (reviewed by Argon; Astra out of quota — `docs/brainstorm/argon_m7.md`)

> **For agentic workers:** Implement task-by-task, test-first. Do NOT commit. Read `AGENTS.md`, `docs/SPEC.md` (§2, §8, §9, §11), `docs/FEASIBILITY.md` (audio-separator 0.30.2 quirks), `docs/brainstorm/argon_m7.md` (source-level findings — follow them), `backend/media/dfn.py` + `backend/media/chunks.py` (M6 chunking/OOM pattern) first. M1–M6b are done.

**Goal:** `separate` ("Separate voice and music") keeps the voice (remove music), keeps the music (karaoke), or exports both, for audio and video, GPU with CPU fallback, bounded VRAM/RAM, with first-run model download progress and offline safety.

## Global Constraints
- Engines: **`htdemucs` default** ("Balanced", Demucs v4, 638 MB VRAM measured) and **`kim_vocal_2`** option ("Tighter music removal", MDX ONNX, 2.3 GB VRAM). No Roformer models (too heavy for 4 GB fp32).
- All audio-separator use is isolated in `backend/media/separator.py` (PATH to tools/ffmpeg, output_dir chdir workaround, CPU forcing via `torch.cuda.is_available` patch for torch and `CPUExecutionProvider` for ONNX).
- `Separator(..., normalization_threshold=1.0, amplification_threshold=0.0)` — the default 0.9 peak-normalizes each window and causes gain jumps across windows.
- MDX: keep `invert_using_spec=False` (True triggers an upstream soundfile write bug → all-zero int16 / wrong-shape files). Keep `segment_size=256` always (any other value silently switches to onnx2torch/PyTorch).
- **OOM ladder:** Demucs segment 7 → 4 → CPU. MDX: GPU → **CPU directly**. ONNX OOM detected by message substrings ("out of memory", "CUDNN_STATUS_ALLOC_FAILED", "Failed to allocate"). Warning `gpu_fallback{op_id}`; stage records actual device.
- **Windows:** ≤ 10 min windows with 3 s raised-cosine crossfades via `backend/media/chunks.py`; **skip all-silent windows** (copy silence through — the library raises / divides by zero on them).
- **Rates:** each window resampled 48 000 → 44 100 with soxr VHQ before separation (so the library's load is a no-op) and 44 100 → 48 000 after, then pad/trim to the exact frame count. Never pass 44.1 kHz audio to another op.
- Mono in → duplicate to stereo for the model → average back to mono. Stereo stays stereo.
- `output`: `vocals` → main = voice; `instrumental` → main = music: Demucs `drums+bass+other` summed in float32 (not mix − vocals: avoids "ghost talk"); MDX `mix − vocals`; `both` → main = voice, plus voice and music stems as extra outputs **encoded into `job_root/output/`** with the job's audio format (add `out_dir`, `audio_format`, `display_stem` to `OpContext`) as `<stem>_voice.<ext>` / `<stem>_music.<ext>`, kind `stem`.
- Weights loaded once per op (Demucs reloads `.th` per `separate()` call — construct the separator once and reuse its loaded model across windows if the API allows; otherwise accept the reload and measure).
- **Download + offline:** wrap `Separator.download_file_if_not_exists` to emit `model_downloading{model_id, pct}`; capabilities must also check the library's metadata JSONs (`download_checks.json`, `vr_model_data.json`, `mdx_model_data.json`) are cached under `models/audio-separator`; offline + missing → op unavailable (`model_missing`), never crash. Add `scripts/prefetch_models.py` entries for both models + JSONs.
- Order stays 30 (before declick/denoise/dereverb/breaths/enhance/silence). Karaoke (`output=instrumental`) additionally disables **declick and dereverb** (and breaths), not only denoise/enhance/silence-vad — update `rules.py` + messages.
- Message codes: `separated{output}`, `gpu_fallback{op_id}`, `model_downloading{model_id,pct}`.

## Review Focus
1. **Synthetic fixture** (extend `scripts/make_fixtures.py`): music = harmonic sawtooth/triangle chords changing every 2 s (LP 3.5 kHz) + bassline + drum transients; mix with `speech.wav` at −10 dB SMR including a 1 s music-only intro and a music-only gap; ground-truth stems saved. Vocals ΔSI-SDR ≥ +8 dB, instrumental ΔSI-SDR ≥ +6 dB; music-only intro/gap RMS reduced ≥ 15 dB in vocals; whisper transcript keeps "quick", "brown", "fox".
2. Karaoke: Silero VAD speech ratio < 10 % of input's; 300–3000 Hz vocal-band energy reduced ≥ 12 dB.
3. Rate round trip: 48 kHz out, exact length, 1 kHz tone stays 1000 ± 2 Hz through the MDX residual path.
4. OOM ladder (monkeypatched) for both engines; silent window skipped without error; gain continuity across a window boundary (no step > 1 dB in a steady tone).
5. 20-min stereo htdemucs on GPU: VRAM delta < 3 GB, RSS < 3 GB (slow).

## Tasks
1. `backend/media/separator.py` engine wrapper (all constraints above) + tests.
2. `separate` op + registry params (`output`: "Keep voice" / "Keep music (karaoke)" / "Export both"; `model`: "Balanced" htdemucs / "Tighter music removal" kim_vocal_2) + `OpContext` additions + runner handling of encoded extra outputs + karaoke rules + tests.
3. Video: vocals/instrumental on video keeps `-c:v copy` (identity timeline) — e2e test with clip.mp4.
4. Review files (`scripts/make_review_files.py`): each real recording + synthetic music bed at −10 dB → `data/review/m7_<stem>_with_music.wav`, `m7_<stem>_voice.wav`, `m7_<stem>_music.wav`; report SI-SDR where ground truth exists and seconds per device.
- [ ] `uv run pytest -m "not slow"` passes; slow tests run once.
