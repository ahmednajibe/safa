Here is my proposal for **LocalAudio Studio**, engineered specifically around the realities of a **Windows 11 + i5-10300H + GTX 1650 (4 GB VRAM)** machine.

---

## 1. Feature List & Hardware-Matched Model Picks

> **Hardware Reality Check:** The GTX 1650 (Turing TU117) has **4 GB VRAM** and **no Tensor Cores** (no `bfloat16`, limited `fp16` speedup). Models must run in `float32` or `int8` in small sliding-window chunks, and **only one model may reside in VRAM at a time**.

| Operation | What It Does | Best Open-Source Tool / Model (Why) | Speed on GTX 1650 (4 GB) / CPU Fallback |
| :--- | :--- | :--- | :--- |
| **1. Remove Music (Source Separation)** | Strips background music/beds while keeping speech intact. | **`audio-separator`** running **`Kim_Vocal_2.onnx`** (via `onnxruntime-gpu`) or **`htdemucs`** (`segment=7`). *Why:* `Kim_Vocal_2` ONNX uses ~1.8 GB VRAM and leaves fewer vocal phase artifacts on speech than stock Demucs; `htdemucs` with 7s chunks uses ~2.2 GB VRAM. | **GPU:** ~3x real-time (10 min audio in ~3.3 min)<br>**CPU:** ~0.4x real-time (~25 min) |
| **2. Background Noise Reduction** | Removes fan hum, HVAC, mic hiss, street noise, and keyboard clicks. | **`DeepFilterNet3`** (PyTorch/Rust, 48 kHz native). *Why:* Best speech-quality-to-compute ratio in open source. Uses <500 MB RAM/VRAM, runs at full 48 kHz bandwidth, and exposes an `atten_lim_db` knob (default `18–24 dB`) so voices never sound robotic. | **GPU:** ~20x real-time (10 min in ~30s)<br>**CPU:** ~5x real-time (10 min in ~2 min) |
| **3. Speech Enhancement & Quality Boost** | Fixes muffled/distant mics, restores high frequencies, and evens out vocal tone. | **Two-tier approach:**<br>• **Tier A (Default — Studio DSP):** Spotify **`pedalboard`** (High-pass @ 80 Hz $\to$ De-esser $\to$ 3-band Presence EQ $\to$ Compressor).<br>• **Tier B (Toggle — AI Restoration):** **`resemble-enhance`** (CFM enhancer configured at `nfe=32`, 15s chunks). *Why:* Full `resemble-enhance` (NFE=64) is slow on a 1650; DSP handles 90% of users instantly, while Tier B rescues severely degraded recordings. | **Tier A (CPU):** ~50x real-time (10 min in ~12s)<br>**Tier B (GPU):** ~0.9x real-time (10 min in ~11 min) |
| **4. Cut Silent Parts (Smart Trim)** | Detects pauses and either removes them or shortens long pauses (e.g., `>0.6s` $\to$ `0.25s`) with 15 ms cosine crossfades. | **`silero-vad`** (ONNX) + `soundfile`/`ffmpeg` slicer. *Why:* Amplitude gates (`pydub`) fail on breath noises or hum. Silero VAD is 2 MB, speech-aware, and immune to noise floor shifts. | **CPU:** ~200x real-time (10 min scanned & sliced in ~4s; video jump-cut re-encode in ~35s via `h264_nvenc`) |
| **5. [Extra] De-Reverb (Room Echo Removal)** | Strips boxy bedroom/office slapback echo without flattening vocal tone. | **`audio-separator`** with **`Reverb_HQ_By_FoxJoy.onnx`** (or **`nara_wpe`** for fast linear dereverb). *Why:* Denoisers struggle with late room reflections; dedicated dereverb ONNX models fix "untreated room" syndrome. | **GPU:** ~3x real-time (10 min in ~3.3 min) |
| **6. [Extra] De-Plosive & De-Esser** | Tames harsh "S/T" sibilance (5–9 kHz) often exaggerated by AI denoisers, plus mic pops. | **`pedalboard`** split-band dynamic compressor + 80 Hz high-pass filter. *Why:* Essential post-AI cleanup; zero VRAM cost. | **CPU:** ~60x real-time (<10s for 10 min) |
| **7. [Extra] Loudness Normalization (LUFS)** | Levels volume to `-14 LUFS` (YouTube) or `-16 LUFS` (Podcasts) with `-1.0 dBTP` true-peak limiting. | **`pyloudnorm`** + **`pedalboard.Limiter`** (or 2-pass `ffmpeg loudnorm`). *Why:* Users equate "louder and consistent" with "higher quality." | **CPU:** ~60x real-time (~10s for 10 min) |
| **8. [Extra] Auto-Subtitles (`.srt`) & Filler Detection** | Generates word-level `.srt`/`.vtt` captions and flags "um/uh" timestamps for optional cutting. | **`faster-whisper`** (`distil-large-v3` or `small` in `int8_float32` on CUDA). *Why:* CTranslate2 `int8` runs fast on GTX 1650 (~1.6 GB VRAM) and pairs naturally with video cleanup. | **GPU:** ~10x real-time (10 min in ~1 min) |

---

## 2. Canonical Pipeline Order (And Why)

When multiple operations are checked, the backend **ignores the order the user clicked them** and enforces this strict acoustic pipeline:

1. **Ingest & Decode (`ffmpeg`)** $\to$ Extract audio to uncompressed `48 kHz, 32-bit float WAV`.
2. **High-Pass Filter (`pedalboard`, 75 Hz)** $\to$ Strip sub-bass rumble and DC offset first so low-end energy doesn't bias neural network normalization.
3. **Remove Music (`Kim_Vocal_2` / `htdemucs`)** $\to$ **Must precede denoising.** Separators are trained on natural mixes; denoising first creates spectral holes that cause vocal phasing during separation.
4. **De-Reverb (`Reverb_HQ`) $\to$ Denoise (`DeepFilterNet3`)** $\to$ Strip room reflections and stationary/non-stationary noise from the isolated voice while preserving natural pause timing.
5. **AI Speech Restoration (`resemble-enhance`, if enabled)** $\to$ Reconstruct upper harmonics *only after* noise is gone (otherwise the generative enhancer hallucinates noise into metallic speech artifacts).
6. **Studio DSP Polish (`pedalboard`: De-Esser $\to$ EQ $\to$ Compressor)** $\to$ Tame any sibilance introduced by neural enhancement and even out vocal dynamics.
7. **Cut Silent Parts (`silero-vad` + Crossfade Slicer)** $\to$ **Must run after separation/denoising.**
   - *Why:* (a) Music/noise masks silence if run early; (b) hard-cutting audio *before* neural inference causes boundary clicks at every cut; (c) for video inputs, VAD keep-timestamps drive synchronized audio+video segment trimming.
8. **Loudness Normalization & True-Peak Limiter (`-14` or `-16 LUFS`, `-1.0 dBTP`)** $\to$ **Always last.** Trimming silence alters integrated LUFS, and the final brickwall limiter guarantees zero clipping before export.
9. **Encode / Video Remux (`ffmpeg`)** $\to$ Export final audio (`WAV`/`MP3`/`AAC`) and remux into video if applicable.

---

## 3. Architecture

- **Backend:** **FastAPI** + **SQLite** (job state/history) + **In-Process Single-Worker Queue** (`asyncio.Queue` feeding a dedicated `ThreadPoolExecutor(max_workers=1)`).
  - *Why no Celery/Redis?* Redis on Windows is unnecessary friction, and a 4 GB GPU can only run **one** job at a time without OOMing.
- **Sequential VRAM Manager:** Each stage runs inside a context manager that loads its model, processes audio in 10–15s overlap-add chunks, writes an intermediate `stage_N.wav` to disk, and immediately calls `del model; gc.collect(); torch.cuda.empty_cache()`. Peak VRAM never exceeds **2.8 GB**.
- **Progress Reporting:** **Server-Sent Events (SSE)** (`GET /api/jobs/{id}/events`) streaming `{ stage, stage_idx, total_stages, stage_pct, overall_pct, eta_sec, vram_mb }` driven by chunk callbacks.
- **Frontend:** **React 19 + Vite + TypeScript + Tailwind CSS + Wavesurfer.js** (runs natively on Node 25). In production, FastAPI serves the static `frontend/dist` build so the user starts everything with a single `start.bat`.
- **FFmpeg Handling:** Auto-provisioned via the **`static-ffmpeg`** Python wheel (or a first-run bootstrap script downloading a pinned `ffmpeg`/`ffprobe` build into `.bin/`). Zero manual `PATH` setup required.
- **Video Pipeline:**
  - **Without Silence Cutting:** Extract audio $\to$ run pipeline $\to$ `ffmpeg -i input.mp4 -i clean.wav -map 0:v:0 -map 1:a:0 -c:v copy -c:a aac -b:a 256k output.mp4`. **Zero video re-encoding** (finishes in <2 seconds with 100% original video quality).
  - **With Silence Cutting:** Pass VAD keep-intervals into an `ffmpeg` `trim`/`atrim` + `concat` filtergraph encoded with **`h264_nvenc`** (`-cq 20`), using the GTX 1650's dedicated NVENC hardware encoder to keep lip-sync locked.
- **File Storage & Cleanup:** `./workspace/jobs/{job_id}/`. Intermediate stage WAVs are deleted as soon as the next stage succeeds; a startup sweep purges jobs older than 24 hours.

---

## 4. Python 3.13 Compatibility Risks & Mitigation

**The Risks:** Python 3.13 breaks the audio ML stack on Windows in three places:
1. **PEP 594 Removals:** `audioop`, `aifc`, and `sunau` were removed from the Python 3.13 standard library, crashing `pydub`, `audioread`, and older `librosa` code paths.
2. **Missing Prebuilt Wheels:** `deepfilternet` (Rust/PyO3 `df` crate) and `onnxruntime-gpu` / `torchaudio` often lack Python 3.13 Windows wheels or require complex Rust/MSVC toolchains.
3. **`resemble-enhance` + `deepspeed`:** `resemble-enhance` lists `deepspeed` as a dependency, which fails to compile on Windows Python 3.13 (and is only needed for training, not inference).

**The Fix:**
- **Pin the app venv to Python 3.11 via `uv`:** Even with Python 3.13 installed globally, `uv` transparently downloads and isolates Python 3.11 in seconds:
  ```powershell
  uv python install 3.11
  uv venv --python 3.11
  ```
- Avoid `pydub` entirely; use `soundfile` (`libsndfile`) and `ffmpeg` subprocesses.
- Install `resemble-enhance` with `--no-deps` (or patch out its `deepspeed` import) since inference only requires `torch`, `torchaudio`, `scipy`, and `einops`.

---

## 5. UX Design

A focused, single-page dark-mode studio layout:
1. **Upload & Media Inspector:** Drag-and-drop zone showing duration, sample rate, integrated LUFS, and video codec/resolution.
2. **One-Click Presets + Ordered Pipeline Stack:**
   - Presets: *Podcast Cleanup*, *Strip Background Music*, *Fix Echoey Room*, *Video Jump-Cutter*.
   - Below presets, operations appear as toggleable cards **stacked in their actual execution order** with 1–2 simple sliders each (e.g., *Denoise Limit: 18 dB*, *Max Pause: 0.5s*, *Target: -14 LUFS*).
3. **"Preview 15s Sample" Button:** Lets the user drag a 15-second region on the waveform and run the selected pipeline on just that slice in ~4 seconds before committing to a 20-minute video.
4. **Synchronized A/B Waveform & Video Player (`Wavesurfer.js`):**
   - Instant **Original / Processed toggle** (hotkey `Tab`) that cross-switches audio mid-playback without losing playhead position, synced to the `<video>` preview element.
5. **Stage Stepper & Download Bar:** Live stage progress pills, GPU/CPU badge, cancel button, and one-click downloads (`MP4`, `WAV`, `MP3 320k`, `.SRT`).

---

## 6. Project Structure & Implementation Plan

```text
local-audio-studio/
├── .python-version              # 3.11
├── pyproject.toml               # uv dependencies + PyTorch cu121 index
├── start.bat                    # One-click launcher
├── backend/
│   ├── main.py                  # FastAPI entry + static UI mount
│   ├── api/                     # routes.py, sse.py
│   ├── core/                    # ffmpeg.py, vram.py, models_hub.py
│   └── pipeline/
│       ├── runner.py            # Sequential queue & VRAM teardown
│       └── stages/              # separate.py, denoise.py, enhance.py, dsp.py, silence.py, loudness.py
└── frontend/                    # React + Vite + TS + Tailwind + Wavesurfer.js
```

### Milestones (Hand-off Ready for Coding Agents)
1. **M1 — Env, FFmpeg & GPU Probe:** Configure `uv` with Python 3.11, PyTorch `cu121`, `static-ffmpeg`, and FastAPI.
   - *Done check:* `GET /api/health` returns `cuda_available: true`, `GTX 1650 (4096 MB)`, and valid `ffmpeg`/`ffprobe` paths.
2. **M2 — Media Ingest, Video Remux & SSE Queue:** Upload endpoint, 48 kHz float32 WAV extraction, `-c:v copy` video remuxing, SQLite job state, and SSE progress stream.
   - *Done check:* Integration test uploads a 10s `.mp4`, streams SSE events to 100%, and outputs a valid `.mp4`.
3. **M3 — DSP, Loudness & Silence Cutting (`pedalboard`, `pyloudnorm`, `silero-vad`):** Implement EQ/De-esser/Compressor, LUFS mastering, and VAD silence cutting (audio crossfades + video `h264_nvenc` segment concat).
   - *Done check:* Pytest verifies synthetic audio with 4s silence is trimmed accurately and normalized within $\pm 0.2\text{ LU}$ of `-14 LUFS`.
4. **M4 — AI Separation & Denoising (`audio-separator` & `DeepFilterNet3`):** Implement chunked GPU stages with strict VRAM teardown and first-run model download progress events.
   - *Done check:* Process a 60s noisy music+speech file; peak CUDA memory stays `<2.8 GB` and output is clean isolated speech.
5. **M5 — 15s Preview Endpoint & Optional `resemble-enhance`:** Add `/api/jobs/{id}/preview` (15s slice) and `deepspeed`-free `resemble-enhance` stage.
   - *Done check:* 15s preview completes in `<15s` on GTX 1650 without OOM.
6. **M6 — React Studio UI & A/B Player:** Build the Vite frontend with `Wavesurfer.js` A/B switching, video sync, preset cards, and `start.bat`.
   - *Done check:* End-to-end browser run uploads a video, previews 15s, runs full pipeline, toggles A/B audio, and downloads the cleaned `.mp4`.

---

## 7. Top Risks & Mitigations

1. **4 GB VRAM OOM Crashes:** Never load two models at once; enforce 10–15s chunked inference; wrap every GPU stage in a `try/except torch.cuda.OutOfMemoryError` that automatically clears cache and retries on CPU (`8 threads`).
2. **Video/Audio Desync After Silence Cutting:** Never cut audio independently when processing video; compute speech intervals once via `silero-vad` and drive both video and audio cuts from the exact same timestamp list in `ffmpeg`.
3. **Windows File Locking (`PermissionError`):** Always close Python `soundfile` descriptors before spawning `ffmpeg` subprocesses on the same path.
4. **First-Run Model Download Hang:** Stream HuggingFace/ONNX model weight download progress over SSE so the UI shows `"Downloading model (first run only): 45%"` instead of appearing frozen.
