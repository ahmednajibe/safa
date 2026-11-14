# Sound System — Final Spec

Merged from three proposals (`docs/brainstorm/astra.md`, `argon.md`, `opus_proposal.md`).
Local-only web app: upload audio/video → pick operations → pipeline runs → download results.

## 1. Target machine
Windows 11, i5-10300H (8 threads), 16 GB RAM, GTX 1650 4 GB (no Tensor Cores → fp32/int8 only), Python 3.13 global (untouched), Node 25, uv. Must work offline after first-time model download. Bind to `127.0.0.1` only.

## 2. Operations (user picks any combination)
Every operation has an `id`, fixed `order` number, params schema, and declared device needs. CPU fallback is mandatory for every GPU op.

| order | id | Name | Engine | Default params |
|---|---|---|---|---|
| 10 | `trim` | Trim start/end | ffmpeg | start/end seconds |
| 15 | `channel` | Channel fix (mono / pick L or R) | ffmpeg | mode=keep |
| 20 | `highpass` | Rumble/DC removal | pedalboard / ffmpeg | 75 Hz |
| 25 | `dehum` | Hum removal 50/60 Hz + harmonics | ffmpeg notch (`bandreject` / `anequalizer`) | 50 Hz, 4 harmonics |
| 30 | `separate` | **Remove music** (keep voice) / **Karaoke** (keep music) / export both stems | `audio-separator` (htdemucs default; MDX `Kim_Vocal_2.onnx` option) | output=vocals |
| 35 | `dereverb` | Room echo removal | `audio-separator` MDX reverb model (optional, behind feasibility check) | — |
| 40 | `denoise` | **Noise reduction** | **DeepFilterNet3** (primary), ffmpeg `afftdn` (fallback / "classic" mode) | atten_lim_db=24 |
| 45 | `declick` | Click/pop removal | ffmpeg `adeclick` | conservative |
| 50 | `enhance` | **Voice enhance / quality** (studio DSP: HPF→de-esser→presence EQ→compressor) | `pedalboard` | preset=voice |
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
- **Launcher**: `start.bat` (setup if needed → start server → open browser at http://127.0.0.1:8765).
- Always pass subprocess args as lists (Arabic / space-containing Windows paths). Avoid `pydub` (audioop removed in 3.13; keep code 3.13-clean anyway). Close file handles before ffmpeg touches the same path.

## 5. API (initial)
- `GET /api/health` — python, torch, cuda, gpu name/VRAM, ffmpeg/ffprobe paths+versions, feature availability per op.
- `GET /api/operations` — op list with params schema, order, availability, conflict rules, presets.
- `POST /api/media` (multipart upload) → `{media_id, probe}`; `GET /api/media/{id}/peaks` waveform peaks.
- `POST /api/jobs` `{media_id, ops:[{id, params}], output:{audio_format, video:bool}, preview:{start,end}|null}` → job.
- `GET /api/jobs`, `GET /api/jobs/{id}`, `GET /api/jobs/{id}/events` (SSE), `POST /api/jobs/{id}/cancel`, `POST /api/jobs/{id}/retry`, `DELETE /api/jobs/{id}`.
- `GET /api/jobs/{id}/files/{name}` — download outputs (audio, video, srt, stems).

## 6. UX
Single-page dark studio:
1. Drop zone → media inspector (duration, sample rate, channels, codec, resolution, integrated LUFS) + audio track picker for multi-track video.
2. Presets: Podcast cleanup, Remove background music, Fix echoey room, Lecture, Karaoke, Video jump-cut.
3. Operation cards in execution order, toggle + 1–3 simple controls each, conflict hints.
4. Waveform with draggable preview region → "Preview" button.
5. Stage stepper with progress, ETA, GPU/CPU badge, cancel.
6. Result: A/B toggle (hotkey Tab, keeps playhead) on waveform + video element, cut regions highlighted, downloads (MP4, WAV, FLAC, MP3, SRT, stems).
7. History list (past jobs, re-download, delete).

## 7. Acceptance
Arabic filename + spaces; 60 s noisy speech+music fixture; 10 s mp4; long (≥30 min) audio within RAM limits; offline run after models cached; peak VRAM < 3 GB.
