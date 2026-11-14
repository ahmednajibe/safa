# Claude Opus 5.5 proposal (written before reading Astra/Argon)

## Features (GTX 1650 4 GB realistic)
| Op | Library | Notes |
|---|---|---|
| Remove music / isolate voice | **Demucs v4 (htdemucs)**, `--two-stems vocals`, segment small for 4 GB | GPU ~0.3x realtime, CPU ~1-2x realtime |
| Noise reduction (AI) | **DeepFilterNet 3** | fast, CPU realtime OK; speech-focused |
| Noise reduction (classic, fallback) | `noisereduce` (spectral gating) | no model, works for any audio |
| Speech enhance / quality | **resemble-enhance** (denoise+enhance, 44.1k) — heavy; fallback: EQ + de-ess + compression via ffmpeg/pedalboard | resemble-enhance deps are fragile; make it optional |
| Cut silence | `pydub`/`silero-vad` or ffmpeg silencedetect → cut list, keep padding | for video: cut the video too (ffmpeg concat/select) |
| Loudness normalize | ffmpeg `loudnorm` 2-pass (EBU R128, -16 LUFS / -14 LUFS presets) | |
| Extra: remove vocals (karaoke) | Demucs no_vocals stem | free from same model |
| Extra: de-reverb / de-echo | optional (UVR MDX models via `audio-separator`) | |
| Extra: transcription + subtitles (SRT/VTT) | **faster-whisper** small/medium int8 | fits 4 GB |
| Extra: filler-word removal ("um/uh") | whisper word timestamps → cut | later milestone |
| Extra: format convert / trim / speed / pitch | ffmpeg | cheap wins |
| Extra: high/low pass, hum removal (50/60 Hz notch), de-click | ffmpeg filters | |

## Pipeline order
decode → (video: extract audio as 48k WAV) → **music removal** → **noise reduction** → **enhance/EQ/de-hum** → **silence cut** (compute on cleaned audio, apply cut list to video too) → **loudness normalize last** → encode (wav/mp3/flac) → video remux (copy video stream if no cuts, else re-encode).

## Architecture
- Backend: **FastAPI** + a single in-process worker thread with a job queue (one GPU job at a time — 4 GB VRAM). SQLite (or JSON) job store. SSE for progress.
- Python: **uv-managed Python 3.11 venv** (torch/demucs/deepfilternet are safest on 3.11), torch CUDA 12.x wheels.
- ffmpeg: `winget install Gyan.FFmpeg` or download static build into `tools/` and reference it by path.
- Frontend: **React + Vite + TypeScript**, wavesurfer.js for waveforms/A-B, built and served by FastAPI as static files so one command starts everything.
- Storage: `data/jobs/<id>/{input, work, output}`; cleanup jobs older than N hours.
- Each operation = a plugin class with `name, params schema, run(in_wav, out_wav, ctx)`; pipeline sorts selected ops by a fixed `order` number.

## UX
Drop zone → file info (duration, type) → operation cards with toggles + per-op options + presets ("Podcast cleanup", "Karaoke", "Lecture") → Start → step-by-step progress → result: A/B player with waveform, download audio / video / SRT.

## Milestones
1. Scaffold: uv project, FastAPI health, Vite app, ffmpeg check, `run.bat`.
2. Upload + media probe + job model + queue + SSE progress + download (with a pass-through "convert" op).
3. ffmpeg ops: loudnorm, hum removal, high/low pass, format.
4. Silence cut (audio + video).
5. Denoise (DeepFilterNet + noisereduce fallback).
6. Demucs music removal / karaoke.
7. Enhance (resemble-enhance optional) + presets.
8. Whisper subtitles.
9. UI polish: A/B waveform, history, cleanup.
10. Tests + README.

## Risks
Python 3.13 wheels (use 3.11); VRAM OOM (segmenting, one job at a time, CPU fallback); first-run model downloads (pre-download script); long video re-encode times; Windows paths with spaces/Arabic chars (always pass args as lists, never shell strings).
