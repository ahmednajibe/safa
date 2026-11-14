# M3a — Media & Storage Implementation Plan

> **For agentic workers:** Implement task-by-task, test-first. Do NOT commit. Read `AGENTS.md`, `docs/SPEC.md` (§3, §4, §5, §8), `docs/FEASIBILITY.md` first. M1 + M2 are done.

**Goal:** Upload any audio/video file, probe it, store it safely, produce waveform peaks and a browser-playable copy, and run a pass-through pipeline (decode → encode → optional video remux with video stream copied) that the job system (M3b) will call.

**Architecture:** Pure media functions in `backend/media/` (ffmpeg/ffprobe subprocess wrappers with arg lists + soundfile streaming), storage layout helpers, a `run_pipeline()` skeleton in `backend/pipeline/runner.py` with an empty op list, and `/api/media` endpoints.

**Tech Stack:** ffmpeg/ffprobe from `backend/core/ffmpeg.py`, soundfile, numpy, FastAPI UploadFile (streamed), sqlite via `backend/db.py`.

**Spec:** `docs/SPEC.md`

## Global Constraints
- All subprocess calls: arg lists, `creationflags=CREATE_NO_WINDOW` on Windows, stderr captured; never shell strings. Paths may be Arabic/spaces.
- Intermediate audio = 48 kHz **float32 WAV** (`pcm_f32le`). >2 channels are downmixed to stereo; mono stays mono.
- Never load a whole file into RAM: uploads stream to disk in 1 MiB chunks; peaks computed with `soundfile.blocks`.
- Storage layout: `data/media/<media_id>/{original.<ext>, source.wav, peaks.json, playback.m4a}`; `data/jobs/<job_id>/{work/, output/}`.
- Supported input extensions: wav, mp3, flac, m4a, aac, ogg, opus, wma, mp4, mkv, mov, webm, avi. Others → 415 `{"code":"unsupported_type"}`.
- Error responses are JSON `{"code": str, "message": str}` with human-readable messages from `docs/UI_DESIGN.md` §6 where they exist.
- Real recordings in `tests/fixtures/real/` (skip if missing) must be used in tests.

## Review Focus
1. Upload with Arabic + spaces filename → stored display name preserved exactly in DB/API, file on disk saved as `original.<ext>` (no unicode-in-path surprises) (test).
2. Video with no audio stream → 422 `no_audio` "This video has no audio track. Choose another file." and nothing left on disk (test with an ffmpeg-generated testsrc mp4 without audio).
3. Disk nearly full → 507 `insufficient_storage` with needed/free sizes in the message, upload not started (monkeypatch `free_space_bytes`) (test).
4. Multi-audio-stream file (mkv with 2 audio streams, generate with ffmpeg) → probe lists both; decode/peaks honour `stream` param (test).
5. Video seek in browser needs HTTP Range: `GET /api/media/{id}/file` with `Range: bytes=0-99` → 206 and 100 bytes (test).

---

### Task 1: Probe + storage helpers
**Files:** Create `backend/media/__init__.py`, `backend/media/probe.py`, `backend/media/storage.py`, `backend/core/proc.py`; tests `tests/test_media_probe.py`.

**Interfaces — Produces:**
- `proc.py`: `run(args: list[str | Path], timeout: float | None = None) -> subprocess.CompletedProcess` (text, utf-8, errors=replace, CREATE_NO_WINDOW, raises `ProcError(args, returncode, stderr_tail)` on non-zero); `class ProcError(RuntimeError)`.
- `probe.py`: dataclasses `AudioStream(index: int, codec: str, sample_rate: int, channels: int, channel_layout: str | None, language: str | None, title: str | None, duration_s: float | None)`, `VideoStream(index: int, codec: str, width: int, height: int, fps: float | None, duration_s: float | None)`, `MediaProbe(duration_s: float, format_name: str, size_bytes: int, audio: list[AudioStream], video: list[VideoStream])` with property `has_video` and `to_dict()`; `probe(path: Path) -> MediaProbe` (ffprobe `-show_format -show_streams -of json`; ignore attached-pic video streams); `class ProbeError(RuntimeError)`.
- `storage.py`: `media_dir(media_id: str) -> Path`, `job_dir(job_id: str) -> Path` (creates `work/`, `output/`), `free_space_bytes(path: Path) -> int`, `display_name(filename: str) -> str` (strip directories, control chars, keep unicode, max 200 chars), `new_id() -> str` (uuid4 hex), `ALLOWED_EXTS: set[str]`.
- Tests: `test_probe_clip_mp4` (1 h264 video 640x360, 1 audio, duration≈10±0.1), `test_probe_real_m4a` (skip if missing; aac 48000 2ch, duration 19.2±0.1 for `Recording (2).m4a`), `test_probe_multi_audio_mkv` (Review Focus 4; generate in tmp_path), `test_probe_garbage_raises` (text file → ProbeError), `test_display_name_keeps_arabic`.

### Task 2: Decode, encode, remux, peaks, loudness
**Files:** Create `backend/media/decode.py`, `backend/media/encode.py`, `backend/media/peaks.py`, `backend/media/loudness.py`; tests `tests/test_media_codec.py`.

**Interfaces — Produces:**
- `decode.py`: `decode_to_wav(src: Path, dst: Path, stream_index: int, sample_rate: int = 48000, start_s: float | None = None, end_s: float | None = None) -> AudioInfo`; `@dataclass AudioInfo(path: Path, sample_rate: int, channels: int, frames: int)` (`duration_s` property). Uses `-map 0:<stream_index> -vn -acodec pcm_f32le -ar 48000` and `-ac 2` only when source channels > 2.
- `encode.py`: `AUDIO_FORMATS = {"wav": (".wav", pcm_s24le), "flac": (".flac", flac), "mp3": (".mp3", libmp3lame -b:a 320k), "aac": (".m4a", aac -b:a 256k)}`; `encode_audio(src_wav: Path, dst_stem: Path, fmt: str) -> Path` (returns path with extension); `video_container_for(codec: str) -> str` ("mp4" for h264/hevc/av1/mpeg4, else "mkv"); `remux_video(video_src: Path, video_stream_index: int, audio_wav: Path, dst_stem: Path, video_codec: str) -> Path` → `-map 0:<v> -map 1:a:0 -c:v copy -c:a aac -b:a 256k -map_metadata 0 -movflags +faststart` (mp4) / `-c:a aac` mkv; output never shorter than video by more than 1 frame (no `-shortest`); `make_playback_copy(src_wav: Path, dst: Path) -> Path` (AAC 192k .m4a for the browser player).
- `peaks.py`: `compute_peaks(wav: Path, per_second: int = 50, max_points: int = 200_000) -> dict` → `{"sample_rate", "channels", "duration_s", "per_second", "data": [[min,max,...] per channel]}` values rounded to 4 decimals, in −1..1; streaming via `soundfile.blocks`; `per_second` lowered automatically to respect `max_points`.
- `loudness.py`: `integrated_lufs(wav: Path) -> float | None` via ffmpeg `ebur128` (take the **last** `I:` value, per FEASIBILITY.md); None for silence (−70).
- Tests: `test_decode_float32_48k` (subtype FLOAT, sr 48000; real m4a → 2 ch, duration within 25 ms of probe), `test_decode_range` (start 2.0 end 5.0 → 3.0±0.02 s), `test_decode_downmix_6ch` (generate 6-ch wav → 2 ch), `test_encode_each_format` (ffprobe codec names pcm_s24le/flac/mp3/aac), `test_remux_copies_video` (clip.mp4 + decoded wav → output video codec h264, same width/height, video duration equal ±0.05, ffprobe `-count_packets` equal video packet count to source), `test_peaks_shape` (len per channel ≈ duration*per_second ±2, values within −1..1), `test_peaks_respects_max_points`, `test_lufs_speech` (fixture speech.wav → between −40 and −5), `test_lufs_silence_none`.

### Task 3: Pipeline runner skeleton
**Files:** Create `backend/pipeline/__init__.py`, `backend/pipeline/types.py`, `backend/pipeline/runner.py`; tests `tests/test_runner.py`.

**Interfaces — Produces:**
- `types.py`: `@dataclass OutputSpec(audio_format: Literal["wav","flac","mp3","aac"] = "wav", keep_video: bool = True)`; `JobDevice = Literal["gpu","cpu"]`; `resolve_device(job_device: JobDevice, op_id: str, params: dict, caps: Capabilities) -> Literal["gpu","cpu"]` in `backend/pipeline/device.py` exactly per SPEC §9 (separate/denoise-ai/subtitles may use gpu when the engine supports it; denoise classic and every other op → cpu; job_device cpu → always cpu); `estimate_seconds(duration_s: float, ops: list[tuple[str, dict]], device: JobDevice) -> tuple[float, float]` in `backend/pipeline/estimates.py` with factors from docs/FEASIBILITY.md; `GET /api/estimate?media_id=&device=&ops=<json>` → `{"low_s","high_s","bucket"}` with buckets "Under a minute" / "About N min" / "Over 30 min"; `@dataclass OutputFile(name: str, kind: Literal["audio","video","subtitle","stem"], path: Path, size_bytes: int)`; `@dataclass Progress(stage: str, stage_idx: int, total_stages: int, stage_pct: float | None, overall_pct: float | None, message: str | None = None, device: str | None = None)`; `ProgressCb = Callable[[Progress], None]`; `class CancelledError(Exception)`; `CancelCheck = Callable[[], bool]`.
- `runner.py`: `run_pipeline(media_path: Path, probe: MediaProbe, audio_stream_index: int, ops: list[tuple[str, dict]], output: OutputSpec, job_root: Path, on_progress: ProgressCb, should_cancel: CancelCheck, preview: tuple[float, float] | None = None, device: JobDevice = "gpu") -> list[OutputFile]` (when device == "cpu" the runner never touches CUDA; this flag is passed to ops and to video encoding later) — stages: "Reading file" (decode, honoring preview range) → ops (none implemented yet: any op id → `NotImplementedError(f"operation {id} not implemented yet")`) → "Saving audio" (encode) → "Rebuilding video" (remux, only if `probe.has_video and output.keep_video and preview is None`). Output names: `<display stem>_clean.<ext>` (display stem from original filename, sanitized for Windows file names but keeping Arabic). Checks `should_cancel()` between stages → raises `CancelledError`. Deletes `work/` intermediates on success.
- Fix `/api/health`: `device` is "gpu" only if gpu info AND `cuda_dlls` AND torch module present (SPEC §9); test it with monkeypatched capabilities.
- Tests: `test_resolve_device_matrix` (gpu/cpu × separate, denoise ai, denoise classic, subtitles, loudness, no-GPU caps), `test_estimate_buckets` (cpu estimate > gpu estimate for separate; bucket strings), `test_passthrough_audio_real_m4a` (skip if missing; output wav duration within 20 ms of source), `test_passthrough_video` (clip.mp4 → mp4 with h264 copied + aac), `test_preview_range_audio_only` (preview (1,4) on clip.mp4 → only audio output, 3.0±0.03 s), `test_progress_called_in_order`, `test_cancel_between_stages` (should_cancel returns True after first stage → CancelledError, no output files), `test_unknown_op_raises`, `test_arabic_output_name` (Arabic fixture → output filename contains the Arabic stem).

### Task 4: Media API
**Files:** Create `backend/api/media.py`; modify `backend/main.py` (include router); tests `tests/test_api_media.py`.

**Interfaces — Produces:**
- `POST /api/media` (multipart `file`) → 201 `{"media_id","filename","size_bytes","probe": MediaProbe.to_dict(),"lufs": null}`. Flow: ext check (415) → disk check: `free_space_bytes(data_dir) < content_length*4 + 512 MiB` → 507 (use `Content-Length` header when present; else check again after write) → stream to `original.<ext>` in 1 MiB chunks, enforcing `max_upload_mb` (413) → probe (ProbeError → 422 `unreadable` "We couldn't read this file. It may be damaged or not a media file.") → no audio → 422 `no_audio` → insert `media` row. On any failure remove the media dir. Then start a background thread that decodes `source.wav` (first audio stream), computes `peaks.json`, `playback.m4a`, and LUFS, and stores LUFS in `probe_json["lufs"]`.
- `GET /api/media/{id}` → same shape + `"ready": {"peaks": bool, "playback": bool}`, `lufs` filled when known.
- `GET /api/media/{id}/peaks?stream=<index>` → peaks JSON (202 `{"code":"processing"}` while not ready; for a non-default stream compute on demand into `peaks_<index>.json`).
- `GET /api/media/{id}/playback` → `playback.m4a` (FileResponse, Range supported). `GET /api/media/{id}/file` → original file with Range (Review Focus 5) and correct content-type.
- `DELETE /api/media/{id}` → 204, removes dir + row (409 if a job references it and is queued/running — jobs table may be empty for now).
- Tests: upload clip.mp4 (201, has_video true), upload Arabic real-copy (Review Focus 1), upload `.txt` → 415, upload no-audio mp4 → 422 + no dir left (Review Focus 2), disk full → 507 (Review Focus 3), peaks become ready (poll up to 20 s), range request 206, delete removes files.
- [ ] `uv run pytest -v` → all pass (M1/M2 included).

## Final report
Files, commands + outcomes, tests, deviations, open problems.
