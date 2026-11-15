# Sound System — Final Spec

Merged from three proposals (`docs/brainstorm/astra.md`, `argon.md`, `opus_proposal.md`).
Local-only web app: upload audio/video → pick operations → pipeline runs → download results.

## 1. Target machine
Windows 11, i5-10300H (8 threads), 16 GB RAM, GTX 1650 4 GB (Turing CC 7.5, no Tensor Cores; default fp32, whisper int8 — query CTranslate2 supported compute types), Python 3.13 global (untouched), Node 25, uv. Must work offline after first-time model download. Bind to `127.0.0.1` only.

## 2. Operations (user picks any combination)
Every operation has an `id`, fixed `order` number, params schema, and declared device needs. CPU fallback is mandatory for every GPU op.

| order | id | Name | Engine | Default params |
|---|---|---|---|---|
| 10 | `trim` | Trim start/end | ffmpeg | start/end seconds |
| 15 | `channel` | Channel fix (mono / pick L or R) | ffmpeg | mode=keep |
| 20 | `highpass` | Rumble/DC removal | pedalboard / ffmpeg | 90 Hz |
| 25 | `dehum` | Hum removal 50/60 Hz + harmonics | ffmpeg notch (`bandreject` / `anequalizer`) | 50 Hz, 4 harmonics |
| 30 | `separate` | **Remove music** (keep voice) / **Karaoke** (keep music) / export both stems | `audio-separator` (htdemucs default; MDX `Kim_Vocal_2.onnx` option) | output=vocals |
| 35 | `dereverb` | Room echo removal | `audio-separator` MDX reverb model (optional, behind feasibility check) | — |
| 38 | `declick` | Mouth-click removal (saliva / lip smacks), optional digital-crackle mode | numpy/scipy band-limited detector + AR repair (mouth), ffmpeg `adeclick` (crackle) | mode=mouth, sensitivity=normal |
| 40 | `denoise` | **Noise reduction** | **DeepFilterNet3** (primary), ffmpeg `afftdn` (fallback / "classic" mode) | atten_lim_db=24 |
| 50 | `enhance` | **Voice enhance / quality** (studio DSP: 95 Hz HPF ×2 → de-boom EQ → downward expander → compressor → presence EQ → optional de-esser; per-file dynamic thresholds from measured floor/speech levels) | `pedalboard` (NoiseGate/HPF/EQ/compressor) + ffmpeg `deesser` | preset=voice, reduce_boom=on, deess=on |
| 55 | `enhance_ai` | AI restoration (optional, later milestone) | `resemble-enhance` in isolated env, `--no-deps` | off |
| 60 | `silence` | **Cut silence** / shorten pauses | `silero-vad` (speech mode) or ffmpeg `silencedetect` (amplitude mode); 15 ms crossfades; same keep-intervals drive video cuts | max_pause=0.6s → keep 0.25s, pad 0.2s |
| 70 | `speed` | Speed / tempo change (pitch-preserving) | ffmpeg `atempo` | 1.0 |
| 80 | `loudness` | **Loudness normalize** + true-peak limit | ffmpeg 2-pass `loudnorm` | −16 LUFS, −1 dBTP (preset −14 YouTube) |
| 90 | `subtitles` | Transcribe → SRT/VTT (runs on final edited audio) | `faster-whisper` small, int8 (GPU int8_float32 / CPU int8) | language=auto |

Conflict rules (enforced by backend, shown in UI):
- `separate` output=instrumental (karaoke) → disables `denoise`, `enhance`, `silence` (speech-based).
- Only one denoiser engine at a time.
- User click order is ignored; pipeline always runs in `order` sequence; UI shows effective order.

## 3. Pipeline
probe (ffprobe) → choose audio stream → decode to 48 kHz float32 WAV → ops in order (each writes `stage_NN_<op>.wav`, previous intermediate deleted after success) → encode (WAV / FLAC / MP3 320k / AAC) → video:
- no timeline edits: `-map 0:v:0 -map 1:a:0 -c:v copy -c:a aac -b:a 256k` (no video re-encode)
- with `trim`/`silence`/`speed`: one shared keep-interval list applied to video via ffmpeg filtergraph, encoded with `h264_nvenc` (fallback `libx264`), audio-video sync verified.

Adapters resample to model rate (Demucs 44.1k, DFN 48k) and back; preserve stereo unless op requires mono; compensate model latency/length.

## 4. Architecture
- **Python 3.11 venv via uv** (`.python-version` = 3.11). Pinned torch/torchaudio CUDA 12.x wheels (cu121 or cu124) verified on GTX 1650.
- **Backend**: FastAPI + SQLite (`data/app.db`). Job states: queued, running, completed, failed, cancelled, interrupted.
- **Worker**: a supervisor thread in the API starts **one worker subprocess per job** (`python -m backend.worker <job_id>`), one at a time (4 GB VRAM). Worker writes progress to SQLite; cancellation kills the worker process tree (incl. ffmpeg children). On startup, running jobs → interrupted (retry offered).
- **VRAM discipline**: each GPU stage loads its model, processes in overlap-add chunks, frees (`del`, `gc.collect()`, `torch.cuda.empty_cache()`). `torch.cuda.OutOfMemoryError` → retry that stage on CPU and report it.
- **Progress**: SSE `GET /api/jobs/{id}/events` → `{state, stage, stage_idx, total_stages, stage_pct, overall_pct, eta_sec, device, message}`; first-run model download progress reported too. Indeterminate when a library gives no %.
- **Preview**: run the selected pipeline on a user-chosen 15–30 s region before the full job.
- **ffmpeg/ffprobe**: `scripts/setup.ps1` downloads a pinned Windows static build into `tools/ffmpeg/`; app resolves `tools/ffmpeg/bin` first, then PATH. Health check reports both.
- **Storage**: `data/jobs/<uuid>/{input,work,output}`; retention 7 days (configurable), "Delete now" button, disk-space check before start.
- **Frontend**: React + Vite + TypeScript + Tailwind + wavesurfer.js. Built into `frontend/dist` and served by FastAPI. Dev mode uses Vite proxy.
- **Launcher**: `start.bat` (setup if needed → start server → open browser at http://127.0.0.1:8790; port overridable via `SOUND_SYSTEM_PORT`).
- Always pass subprocess args as lists (Arabic / space-containing Windows paths). Avoid `pydub` (audioop removed in 3.13; keep code 3.13-clean anyway). Close file handles before ffmpeg touches the same path.

## 5. API (initial)
- `GET /api/health` — python, torch, cuda, gpu name/VRAM, ffmpeg/ffprobe paths+versions, feature availability per op.
- `GET /api/operations` — op list with params schema, order, availability, conflict rules, presets.
- `POST /api/media` — raw `application/octet-stream` body, original filename percent-encoded in the `X-Filename` header → `{media_id, probe}`; `GET /api/media/{id}/peaks` waveform peaks.
- `POST /api/jobs` `{media_id, ops:{id:params}|[{id,params}], output:{audio_format, keep_video}, device:"gpu"|"cpu"|null, preview:{start_s,end_s}|null, audio_stream:int|null}` → job.
- `GET /api/jobs`, `GET /api/jobs/{id}`, `GET /api/jobs/{id}/events` (SSE), `POST /api/jobs/{id}/cancel`, `POST /api/jobs/{id}/retry`, `DELETE /api/jobs/{id}`.
- `GET /api/jobs/{id}/files/{name}` — download outputs (audio, video, srt, stems).

## 6. UX
Single-page dark studio:
1. Drop zone → media inspector (duration, sample rate, channels, codec, resolution, integrated LUFS) + audio track picker for multi-track video.
2. Presets: Podcast cleanup, Remove background music, Fix echoey room, Lecture, Karaoke, Video jump-cut.
3. Operation cards in execution order, toggle + 1–3 simple controls each, conflict hints.
4. Waveform with draggable preview region → "Preview" button.
5. Stage stepper with progress, ETA, GPU/CPU badge, cancel.
6. Result: A/B toggle (hotkeys A/B, keeps playhead; see docs/UI_DESIGN.md) on waveform + video element, cut regions highlighted, downloads (MP4, WAV, FLAC, MP3, SRT, stems).
7. History list (past jobs, re-download, delete).

## 7. Acceptance
Arabic filename + spaces; 60 s noisy speech+music fixture; 10 s mp4; long (≥30 min) audio within RAM limits; offline run after models cached; peak VRAM < 3 GB.

## 8. Round-2 decisions (from Astra + Argon critiques, `docs/brainstorm/*_review.md`)
- **Candidate pins (M1 must validate on this machine, then freeze in `uv.lock` + `docs/FEASIBILITY.md`):** `torch==2.5.1`, `torchaudio==2.5.1`, `torchvision==0.20.1` from `https://download.pytorch.org/whl/cu124` (torchaudio 2.5.1 still has `torchaudio.backend.common.AudioMetaData` that `deepfilternet==0.5.6` imports — if it fails, add a `sys.modules` shim); `numpy==1.26.4`; `soundfile==0.12.1`; `deepfilternet==0.5.6` + `deepfilterlib==0.5.6`; `audio-separator` pinned to a version whose metadata allows numpy<2 (≈0.30.x) with `diffq-fixed==0.2.4`; `silero-vad==5.1.2`; `pedalboard==0.9.16`; `faster-whisper==1.1.1`, `ctranslate2==4.5.0`.
- **ONNX Runtime:** exactly one distribution installed. Use `onnxruntime-gpu` (CUDA 12 / cuDNN 9) and a uv `override-dependencies` so nothing also pulls `onnxruntime`. If that breaks silero/faster-whisper, fall back to CPU `onnxruntime` and run separation on torch Demucs.
- **CUDA DLLs:** `nvidia-cublas-cu12` + `nvidia-cudnn-cu12` (cuDNN 9) wheels; `backend/core/gpu.py::setup_cuda_dlls()` calls `os.add_dll_directory()` for every `site-packages/nvidia/*/bin` and is called at worker start before importing onnxruntime/ctranslate2.
- **Audio I/O:** use `soundfile` (not torchaudio loaders — static ffmpeg has no shared libs for torchaudio). Process in blocks (`soundfile.blocks` / chunk iterators) for DSP + DeepFilterNet so 60 min audio doesn't load whole into RAM.
- **silero-vad:** PyPI package with bundled weights (no `torch.hub`); run on a 16 kHz mono copy; apply timestamps to the 48 kHz stereo signal.
- **pydub:** allowed only as an indirect dependency of audio-separator; our code never imports it.
- **VRAM:** per-engine cleanup (torch: `empty_cache`; ONNX: drop session; CTranslate2: delete model). Measure total GPU memory via `nvidia-smi` / NVML, not only torch allocator. Engine-specific OOM detection → CPU retry. Demucs segment explicitly limited (start 7 s, drop to 4 s on OOM); MDX `segment_size=256, batch_size=1`.
- **Timeline model:** `backend/media/timeline.py` holds a list of segments `{src_start, src_end, out_start, out_end, speed}`; composition of trim → silence → speed yields one map used for: audio rendering, video rendering, subtitle timing, and A/B playhead translation (output time ↔ source time). Boundaries use short non-overlapping fades (fade-out/fade-in ≤10 ms inside each kept segment), **never overlapping crossfades**, so audio and video durations stay identical.
- **Long cut lists:** ffmpeg filtergraphs written to a file and passed with `-filter_complex_script` (Windows 32 KB command-line limit).
- **Fixtures:** besides synthetic tones, generate real speech via Windows SAPI (`System.Speech`) TTS with known text (for VAD + whisper tests), plus mixes: speech+noise, speech+music, speech with known silences, silence-only file, 10 s mp4, Arabic+spaces filename. Optional: a CC-BY LibriSpeech/LibriVox sample downloaded by script.
- **Model manifest early (M1):** `backend/core/models_manifest.py` lists each model (name, source, filename, cache dir under `models/`, size, sha256 when known); all libraries pointed at `models/` (e.g. `HF_HOME`, DFN model dir, audio-separator `model_file_dir`, faster-whisper `download_root`). Offline mode: if model missing and no network → op reported unavailable, not crash.
- **Real recordings:** `tests/fixtures/real/*.m4a` (user's own speech, 19–33 s, gitignored). Used as acceptance input from M3a onward and for the final end-to-end walkthrough; tests skip when absent.

## 9. Processing device choice (user request 2026-11-15; reviewed by Astra + Argon, `docs/brainstorm/*_device.md`)
- Job-level `device`: `"gpu" | "cpu"`. Default = `"gpu"` when GPU is usable, else `"cpu"`. (No separate "auto": Astra and Argon both showed it behaved identically to gpu.) **No per-step override in v1** (only separate, AI denoise and subtitles use the GPU, and they run one at a time).
- **GPU usable** = `/api/health.device == "gpu"`, which requires nvidia-smi GPU **and** `cuda_dlls` loaded **and** torch module present. A job requesting `gpu` when not usable → 422 `gpu_unavailable`.
- `resolve_device(job_device, op_id, params, caps) -> "gpu" | "cpu"` per stage, using engine capability: separate (torch CUDA for Demucs, ORT `CUDAExecutionProvider` for MDX), denoise only when `engine == "ai"` (classic/afftdn → cpu), subtitles (CTranslate2 CUDA with `int8_float32` supported), everything else cpu.
- **`cpu` is a hard ceiling:** worker process started with `CUDA_VISIBLE_DEVICES=""`; ORT forced to `CPUExecutionProvider`; CT2 `device="cpu"`; video re-encode uses `libx264` (never `h264_nvenc`).
- **GPU out of memory:** first retry on GPU with smaller chunks (Demucs segment 7→4, MDX segment_size 256→128), then free engine resources and retry that stage **once on CPU**. Never prompt mid-job. Persistent visible message: "Graphics memory ran out. Running this step on the processor instead." Detect OOM per engine (`torch.cuda.OutOfMemoryError`; ORT errors containing "out of memory"/"CUDNN_STATUS_ALLOC_FAILED"/"Failed to allocate"; CT2 `RuntimeError` containing "out of memory").
- Job records `requested_device`, and per stage `actual_device` + `fallback_reason`; progress events include the actual device.
- Settings are **snapshotted at submission**; changing the choice affects only new jobs; Retry reuses the snapshot. The UI remembers the last choice in `localStorage`.
- **Estimates:** `backend/pipeline/estimates.py` holds per-engine real-time factors per device from `docs/FEASIBILITY.md` (rough), `estimate_seconds(duration_s, ops, device) -> (low, high)`; UI shows coarse buckets only ("Under a minute", "About 2 min", "About 10 min", "Over 30 min"), labelled as estimates; after fallback the estimate is dropped ("Estimating…"). Later milestones may refine factors from completed jobs.

## 10. Cut silence = remove silence (user clarification 2026-11-15)
- The `silence` op is named **"Cut silence"** and its default action is **remove**: every silent part longer than `min_silence_s` (default **0.3 s**) is removed completely; `pad_s` (default **0.1 s**) of audio is kept on each side of speech so syllables aren't clipped. Result = only the speech, joined with the 10 ms boundary fades from §8.
- Params: `action` select `remove` ("Remove silence", default) | `shorten` ("Shorten pauses"); `min_silence_s` 0.3 (0.1–3.0, label "Shortest silence to cut"); `pad_s` 0.1 (0–0.5, advanced, label "Keep around speech"); `keep_pause_s` 0.25 (only for shorten, label "Pause length after shortening"); `mode` vad | amplitude; `threshold_db` −40 (amplitude only, advanced).
- Leading and trailing silence are always removed in `remove` mode.
- Presets "Podcast cleanup", "Lecture" and "Video jump-cut" use `action: remove`.

## 11. Arabic interface (user request 2026-11-15)
- Top bar language button: shows the *other* language's own name ("العربية" while in English, "English" while in Arabic). Choice saved in `localStorage`; first visit follows `navigator.language` (ar* → Arabic).
- Frontend i18n: `frontend/src/i18n/{en,ar}.ts` dictionaries + `useT()` hook; `<html lang dir>` switches (`dir="rtl"` for Arabic). No library required (small app); keys typed so a missing Arabic key is a TypeScript error.
- Op names, descriptions, param labels and option labels come from the dictionaries **keyed by op id / param key / option value**, not from the backend's English strings (backend strings remain the English fallback).
- **Backend messages are localizable:** every user-facing message from the backend (job `message`, `error`, stage names, op result messages, API error `message`) also carries `message_code` (snake_case) + `message_params` (JSON object), e.g. `{"message_code":"silence_removed","message_params":{"seconds":12.4,"count":37}}`. The English `message` stays for logs/tests. Stage names use the op id.
- RTL rules: layout mirrors with CSS logical properties (`ms-*/me-*/ps-*/pe-*`, `start/end`), icons that imply direction flip, **but the waveform, time ruler, transport and timecodes stay LTR** (`dir="ltr"` islands). Filenames keep `dir="auto"` + bidi isolation. Numbers stay Western digits (0–9) with tabular figures.
- Arabic font: IBM Plex Sans Arabic 400/500/600 via `@fontsource/ibm-plex-sans-arabic` (offline), stacked before Inter when `lang="ar"`; Arabic line-height 1.6 for body text.
- Arabic copy draft: `docs/UI_DESIGN.md` §12 (reviewed by Astra + Argon).
- **Message codes (review additions):** the backend never sends user-facing English-only text. Envelopes: status/progress `{message_code, message_params}`, errors `{code, message, params}`, warnings `{warning_code, warning_params}` (e.g. `gpu_fallback`). Codes at least: `silence_removed{seconds,count}`, `pauses_shortened{count,seconds}`, `no_speech_found`, `very_quiet_raised`, `gpu_fallback{op_id}`, `model_downloading{model_id,pct}`, `job_queued{position}`, `job_interrupted`, `job_cancelled`, `job_failed{op_id?}`, `prep_failed`, `insufficient_storage{needed_gb,free_gb}`, `unsupported_type`, `unreadable`, `no_audio`, `gpu_unavailable`, `too_large`, validation `{param,min,max}`; availability `unavailable_code` (`planned` | `module_missing` | `model_missing`); conflicts `{conflict_code: "karaoke_speech_op", blocker_op_id}`; presets `preset_partial{skipped_op_ids}`; estimates `bucket_code` (`under_1m` | `about_n_m{minutes}` | `over_30m`). Codes/params persisted in SQLite and sent over SSE; unknown codes fall back to the English `message`; raw numbers only (formatting is the frontend's job).
