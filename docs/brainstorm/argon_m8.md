### 1–5. Open Questions Answered

- **Q1 — Models, Compute Types, VRAM & Speed ([utils.py](file:///F:/sound_system/.venv/Lib/site-packages/faster_whisper/utils.py#L12-L31)):**
  - **Names:** `"small"` (`Systran/faster-whisper-small`, 80 mels, 12/12 layers) for `fast` (default); `"large-v3-turbo"` (`mobiuslabsgmbh/faster-whisper-large-v3-turbo`, 128 mels, 32 enc / 4 dec layers) for `accurate`. **Drop `"medium"`**: 24 decoder layers make it ~2.5× slower than `"large-v3-turbo"` with higher Arabic dialectal/MSA WER.
  - **Compute types:** GPU `"int8_float32"` via `ctranslate2.get_supported_compute_types("cuda")` (fallback `"float32"`); CPU `"int8"`, `cpu_threads=4`.
  - **VRAM:** **Confident (measured in [FEASIBILITY.md](file:///F:/sound_system/docs/FEASIBILITY.md#L32)):** `"small"` GPU `int8_float32` = **584 MB** delta (2.87 s on 6 s clip; CPU `int8` = 13.8 s). **Uncertain (unmeasured, estimated from CT2 int8 weights + 128-mel buffers):** `"large-v3-turbo"` GPU `int8_float32` ≈ **1.1–1.5 GB** (~1.6 GB HF fp16 download, quantized to int8 on load), well under the 3 GB cap.
  - **30-min runtime (post-VAD):** `"small"` GPU ~1.5–2.5 min / CPU ~8–14 min; `"large-v3-turbo"` GPU ~4–6 min / CPU fallback ~18–28 min.

- **Q2 — Hallucinations & Arabic–English Code-Switching ([transcribe.py](file:///F:/sound_system/.venv/Lib/site-packages/faster_whisper/transcribe.py#L705-L749)):**
  - Pass `vad_filter=True`, `vad_parameters=dict(threshold=0.5, min_silence_duration_ms=500, speech_pad_ms=200)` (default in [vad.py](file:///F:/sound_system/.venv/Lib/site-packages/faster_whisper/vad.py#L41) is `min_silence_duration_ms=2000`, which leaves 0.5–1.9 s noise gaps where Arabic/English YouTube outros hallucinate).
  - Pass `word_timestamps=True`, `hallucination_silence_threshold=2.0`, `condition_on_previous_text=False`, `repetition_penalty=1.1`, `no_repeat_ngram_size=3`, `no_speech_threshold=0.6`, `log_prob_threshold=-1.0`, `compression_ratio_threshold=2.4`.
  - **Code-switching:** Keep `multilingual=False` ([transcribe.py](file:///F:/sound_system/.venv/Lib/site-packages/faster_whisper/transcribe.py#L1150-L1156) per-segment detection has no threshold guard and flips short windows to random languages). Set `language_detection_segments=4` so `auto` samples up to 2 min instead of only the first 30 s.

- **Q3 — Arabic Subtitle Rules, Punctuation & BOM:**
  - **Bug in [transcribe.py](file:///F:/sound_system/.venv/Lib/site-packages/faster_whisper/transcribe.py#L737-L738):** default `append_punctuations` omits Arabic `،؛؟»`. Pass `append_punctuations="\"'.。,，!！?？:：”)]}、،؛؟»"` and `prepend_punctuations="\"'“¿([{-«"` so `merge_punctuations` attaches `؟`/`،` to words instead of emitting orphan punctuation tokens.
  - **RTL & BOM:** Prefix lines containing `[\u0600-\u06FF]` with `\u200f` (RLM) so trailing punctuation and English words don't jump in BiDi players (exclude `\u200f` from the 42-char count). Write **SRT as `utf-8-sig` (with BOM)** for Windows player compatibility; **VTT as `utf-8` (no BOM)**.

- **Q4 — Hard Subs:**
  - **Defer.** Burning in breaks `-c:v copy` on uncut video ([SPEC.md](file:///F:/sound_system/docs/SPEC.md#L36)), requires `libass` Arabic shaping + bundled font path escaping in ffmpeg filtergraphs, and isn't in [UI_DESIGN.md](file:///F:/sound_system/docs/UI_DESIGN.md#L120).

- **Q5 — Preview Interactions:**
  - **Bug in [trim.py](file:///F:/sound_system/backend/operations/impl/trim.py#L21-L26):** `_decode` ([runner.py](file:///F:/sound_system/backend/pipeline/runner.py#L126-L135)) slices `source.wav` to `[0, end_s - start_s]`, but `trim` compares full-file `start_s`/`end_s` against `ctx.duration_s` and raises `ValueError` when `trim.start_s >= preview_len`. Shift `trim` bounds by `-preview[0]` (or skip `trim` in preview).
  - `dfn`, `wpe` (20 s chunks), `room` (≥85 ms), `enhance`, `loudness`, `silence`, and `speed` all work directly on 15–30 s regions.

### Wrong or Missing in Draft

- **Empty/silent audio crash ([transcribe.py](file:///F:/sound_system/.venv/Lib/site-packages/faster_whisper/transcribe.py#L848-L904)):** With `vad_filter=True` and `language=None`, 0 speech chunks produce 0-frame features and `detect_language` raises `ValueError: max() arg is an empty sequence`. Guard empty/silent input before calling `transcribe` and write empty SRT/VTT (`count=0`).
- **Manifest & cache collision ([capabilities.py](file:///F:/sound_system/backend/core/capabilities.py#L76-L77), [models_manifest.py](file:///F:/sound_system/backend/core/models_manifest.py#L59-L66)):** Add `whisper_turbo`; check `models--Systran--faster-whisper-small` and `models--mobiuslabsgmbh--faster-whisper-large-v3-turbo` specifically (not `models--*`).
- **Progress, Cancel & Output Lifetime:** Iterate the lazy `segments` generator checking `ctx.should_cancel()` and reporting `seg.end / duration`; pass `out_dir` + `f"{stem}_{suffix}"` on [OpContext](file:///F:/sound_system/backend/operations/base.py#L27-L41) so `.srt`/`.vtt` aren't deleted with `work/`; disable `subtitles` under Karaoke in [rules.py](file:///F:/sound_system/backend/operations/rules.py#L14-L18); add `"ur"`, `model`, `max_line_chars`, `max_lines` to [registry.py](file:///F:/sound_system/backend/operations/registry.py#L236-L253) and RTFs to [estimates.py](file:///F:/sound_system/backend/pipeline/estimates.py#L18-L19).

### Objective Tests to Add

- **Silence-only WAV (`language="auto"`, `vad_filter=True`):** No `ValueError`, emits valid empty `.srt`/`.vtt` and `count=0`.
- **Arabic + English cue segmenter property test:** `،؛؟` attach to preceding words, lines with Arabic start with `\u200f`, visible length ≤ 42, ≤ 2 lines, duration 1–7 s, gap ≥ 80 ms, SRT has BOM, VTT has no BOM.
- **Preview + `trim` + `silence` + `loudness` + `subtitles`:** Preview on `[20, 40]` with `trim(start_s=25, end_s=50)` succeeds; cue timestamps stay within `[0, preview_out_duration]`.
- **CT2 OOM → CPU retry & mid-generator cancel:** Monkeypatched `RuntimeError("out of memory")` frees model, retries CPU `int8`, and emits `gpu_fallback{"op_id": "subtitles"}`.
