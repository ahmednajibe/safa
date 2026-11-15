# Roadmap

Each milestone gets a detailed task plan in `docs/superpowers/plans/` written right before it is delegated to Devin SWE-2 Max. The orchestrator (Claude) reviews each result, runs the done checks, sends issues back, and commits. Spec: `docs/SPEC.md` (§8 = round-2 decisions, they override earlier sections).

| # | Milestone | Done check |
|---|---|---|
| M1 | **Environment, fixtures & feasibility gate**: uv/Py3.11 project with pins, CUDA DLL setup, ffmpeg in `tools/`, model manifest + `models/` cache, fixtures (SAPI speech etc.), `scripts/feasibility.py` | Script exits 0; every engine OK on CPU, GPU result + time + peak VRAM recorded in `docs/FEASIBILITY.md` from a real run |
| M2 | **App shell**: FastAPI app, settings, SQLite, `/api/health`, `/api/operations` registry skeleton (all ops, availability from feasibility), Vite+React+TS+Tailwind scaffold served by FastAPI, `start.bat` | `start.bat` opens page showing health; `uv run pytest` green |
| M3a | **Media & storage**: upload, ffprobe probe, stream selection, decode to 48k float WAV, peaks, job folders, encoders (wav/flac/mp3/aac), video remux `-c:v copy` | pytest: mp4 + Arabic-name upload → probe → pass-through output plays, video stream copied |
| M3b | **Jobs & worker**: SQLite job model, supervisor + one worker subprocess per job, progress, SSE, cancel (kill tree), retry, interrupted-on-restart, downloads | pytest: SSE reaches completed; cancel kills ffmpeg; restart marks interrupted |
| M4a | **Audio DSP ops**: highpass, dehum, declick, enhance (pedalboard + ffmpeg deesser), channel, loudness 2-pass; presets; conflict rules; effective order | per-op tests; loudness ±0.5 LU, TP ≤ −1 dBTP |
| M4b | **Timeline & video**: `timeline.py` segment map, trim + speed ops, video render via `-filter_complex_script`, h264_nvenc → libx264 fallback | audio/video durations equal within 1 frame; map round-trips times |
| M4c | **Voice fixes from user listening** (mouth-click remover, de-boom voice polish + expander, order/presets) | metrics on real recordings; user listens |
| M5 | **Cut silence (remove by default, SPEC §10)** + Task 0: backend message codes (SPEC §11); was: Silence cutting: silero-vad (16k mono) + silencedetect modes, pause shortening, padding, fades, feeds timeline | known-silence fixture → expected duration ±100 ms; speech kept; video synced |
| M6 | **Noise reduction + room echo** (DFN3 + afftdn; auto-resonance EQ + WPE, per docs/brainstorm/room_findings.md) + per-stage results persisted | noise floor reduced ≥10 dB on fixture; 30 min file within RAM |
| M6b | **Reduce breaths** (frame-level detector, −12 dB attenuation) + silence padding fix — design: docs/brainstorm/argon_breath.md | consonant-safety + synthetic + real-recording metrics |
| M7 | **Separation** (dereverb moved to M6): audio-separator (htdemucs default, MDX option), vocals/instrumental/both, OOM → CPU, download progress | music energy reduced in vocals stem; peak VRAM < 3 GB; CPU path works |
| M8 | **Subtitles & preview**: faster-whisper SRT/VTT mapped through timeline; preview job for 15–30 s region | SAPI fixture transcribed with recognizable words; preview returns audio |
| R1 | **Full backend code review** (user request, after M8 = backend finished): graphify code graph of backend+tests+scripts → Claude line-by-line + test-quality review (do the tests really fail when the code is wrong?) + Argon (+ Astra if quota) independent review → merged findings → one SWE fix round → re-verify | all findings fixed or explicitly accepted; full test suite green |
| M9a | **UI core** (+ Arabic/RTL per SPEC §11 + UI_DESIGN §12, Process-on device control, save location per SPEC §12 + UI_DESIGN §13): upload, media inspector, presets, op cards in effective order with params + conflict hints, progress stepper + cancel, downloads, history | browser walkthrough passes (webapp-testing) |
| M9b | **UI waveform & A/B**: wavesurfer peaks, preview region, A/B toggle (Tab) with playhead translation, video sync, cut regions | browser walkthrough + design review passes |
| M10 | **Hardening**: retention cleanup, disk check, `prefetch_models.py`, offline check, README, final UI polish review | fresh `start.bat` works; offline run works |

UI design direction is decided in a dedicated design round (Claude + Astra + Argon, using design skills) and written to `docs/UI_DESIGN.md` before M9a.
