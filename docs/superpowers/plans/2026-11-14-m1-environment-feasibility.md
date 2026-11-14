# M1 — Environment, Fixtures & Feasibility Gate Implementation Plan

> **For agentic workers:** Implement task-by-task, test-first where a test is given. Do NOT commit (orchestrator commits). Read `AGENTS.md`, `docs/SPEC.md` (esp. §8) first.

**Goal:** A reproducible Python 3.11 environment on this Windows/GTX 1650 machine with ffmpeg, all ML engines installed, models cached under `models/`, test fixtures, and a feasibility script that proves each engine runs on CPU and (where possible) GPU.

**Architecture:** uv-managed project at repo root; shared helpers in `backend/core/`; one-off scripts in `scripts/`; generated fixtures in `tests/fixtures/generated/` (gitignored).

**Tech Stack:** uv, Python 3.11, torch 2.5.1+cu124, deepfilternet 0.5.6, audio-separator, silero-vad, pedalboard, faster-whisper, soundfile, numpy 1.26.4, pytest, ffmpeg 7.1.x static build.

**Spec:** `docs/SPEC.md`

## Global Constraints
- Never delete/modify anything outside `F:\sound_system`.
- `requires-python = ">=3.11,<3.12"`; `.python-version` = `3.11`.
- Exactly one ONNX Runtime distribution (`onnxruntime-gpu`) — use `[tool.uv] override-dependencies` to stop `onnxruntime` from being installed alongside. If impossible, document why and use CPU `onnxruntime`.
- `numpy==1.26.4` (DeepFilterNet breaks on numpy 2).
- torch / torchaudio / torchvision = 2.5.1 / 2.5.1 / 0.20.1 from `https://download.pytorch.org/whl/cu124` (uv explicit index + `[tool.uv.sources]`).
- All subprocess calls use argument lists. Paths may contain Arabic and spaces.
- All model downloads go under `F:\sound_system\models\` (no files in the user profile caches).
- Audio I/O via `soundfile`, never torchaudio loaders.
- If a pinned version cannot install, pick the nearest working version, and record the exact reason in `docs/FEASIBILITY.md`.

## Review Focus
1. ffmpeg found from `tools/ffmpeg/bin` even when not on PATH → `find_ffmpeg()` returns that path (test pins it).
2. CUDA DLL setup when `site-packages/nvidia` is missing → `setup_cuda_dlls()` returns `[]`, does not raise.
3. Fixture file with Arabic + spaces name is readable by soundfile and ffprobe (test).
4. A GPU check failing (OOM / missing DLL) → recorded as GPU FAIL with message, CPU still tested, script still exits 0 if all CPU OK.
5. Running `make_fixtures.py` twice is idempotent (overwrites, no errors).

---

### Task 1: Project + paths + ffmpeg resolution
**Files:**
- Create: `.python-version`, `pyproject.toml`, `backend/__init__.py`, `backend/core/__init__.py`, `backend/core/paths.py`, `backend/core/ffmpeg.py`, `scripts/setup.ps1`, `tests/__init__.py`, `tests/test_core.py`

**Interfaces — Produces:**
- `backend/core/paths.py`: `ROOT: Path` (repo root), `TOOLS_DIR = ROOT/"tools"`, `MODELS_DIR = ROOT/"models"`, `DATA_DIR = ROOT/"data"`, `FIXTURES_DIR = ROOT/"tests"/"fixtures"/"generated"`.
- `backend/core/ffmpeg.py`: `class FfmpegNotFound(RuntimeError)`; `find_ffmpeg() -> Path`; `find_ffprobe() -> Path` (search `TOOLS_DIR/"ffmpeg"/"bin"` first, then `shutil.which`); `ffmpeg_version() -> str` (first line of `ffmpeg -version`); `has_encoder(name: str) -> bool` (parses `ffmpeg -hide_banner -encoders`).

- [ ] Write `pyproject.toml` (project name `sound-system`, deps per Global Constraints and SPEC §8: fastapi, uvicorn[standard], python-multipart, soundfile, numpy, soxr, pedalboard, deepfilternet, deepfilterlib, audio-separator[gpu] (or audio-separator + onnxruntime-gpu), silero-vad, faster-whisper, ctranslate2, nvidia-cublas-cu12, nvidia-cudnn-cu12 (cuDNN 9.x), torch, torchaudio, torchvision; dev group: pytest, httpx).
- [ ] `scripts/setup.ps1`: `uv sync`; if `tools/ffmpeg/bin/ffmpeg.exe` missing, download `https://github.com/GyanD/codexffmpeg/releases/download/7.1.1/ffmpeg-7.1.1-essentials_build.zip` (or nearest existing 7.1.x tag), extract into `tools/ffmpeg/` so `tools/ffmpeg/bin/ffmpeg.exe` + `ffprobe.exe` exist; print versions. Idempotent.
- [ ] Run `powershell -ExecutionPolicy Bypass -File scripts/setup.ps1`; expected: uv sync succeeds, ffmpeg + ffprobe versions printed.
- [ ] Tests in `tests/test_core.py`:
```python
def test_find_ffmpeg_prefers_tools_dir():
    p = find_ffmpeg()
    assert p.exists() and p.parent == TOOLS_DIR / "ffmpeg" / "bin"
def test_find_ffprobe_exists():
    assert find_ffprobe().exists()
def test_ffmpeg_version_string():
    assert ffmpeg_version().startswith("ffmpeg version")
def test_has_libx264():
    assert has_encoder("libx264")
```
- [ ] `uv run pytest tests/test_core.py -v` → PASS.

### Task 2: GPU helpers + model manifest
**Files:** Create `backend/core/gpu.py`, `backend/core/models_manifest.py`; extend `tests/test_core.py`.

**Interfaces — Produces:**
- `gpu.py`: `setup_cuda_dlls() -> list[Path]` (for each `<site-packages>/nvidia/*/bin` dir that exists: `os.add_dll_directory`, prepend to `PATH`; returns the dirs; never raises); `gpu_info() -> dict | None` → `{"name": str, "total_mb": int, "used_mb": int, "driver": str}` via `nvidia-smi --query-gpu=name,memory.total,memory.used,driver_version --format=csv,noheader,nounits`, None if unavailable; `class VramSampler` context manager polling `gpu_info()["used_mb"]` every 0.2 s in a thread, attribute `peak_mb: int` (peak minus baseline at enter).
- `models_manifest.py`: `@dataclass ModelSpec(name: str, engine: str, source: str, filename: str | None, cache_subdir: str, approx_mb: int, sha256: str | None = None)`; `MODELS: dict[str, ModelSpec]` with entries `deepfilternet3`, `htdemucs`, `kim_vocal_2`, `silero_vad` (bundled in package — mark source `"bundled"`), `whisper_small`; `model_dir(name) -> Path` (= `MODELS_DIR/cache_subdir`, created); `configure_model_env() -> None` sets `HF_HOME`, `HF_HUB_CACHE`, `TORCH_HOME`, `XDG_CACHE_HOME` under `MODELS_DIR` (call before importing ML libs).
- [ ] Tests:
```python
def test_setup_cuda_dlls_returns_list():
    assert isinstance(setup_cuda_dlls(), list)
def test_model_dirs_under_models():
    for name in MODELS:
        assert MODELS_DIR in model_dir(name).parents
def test_configure_model_env_sets_hf_home():
    configure_model_env(); assert Path(os.environ["HF_HOME"]).is_relative_to(MODELS_DIR)
```
- [ ] `uv run pytest -v` → PASS.

### Task 3: Fixtures
**Files:** Create `scripts/make_fixtures.py`, `tests/test_fixtures.py`.

**Interfaces — Produces:** `tests/fixtures/generated/` containing (48 kHz unless noted) and `fixtures.json` describing each `{file, duration_s, sample_rate, channels, text?, speech_intervals?, silence_intervals?}`:
- `speech.wav` — mono, Windows SAPI TTS (`System.Speech.Synthesis` via `powershell -NoProfile -Command` with args list; text: "The quick brown fox jumps over the lazy dog. Sound system test number one." ), resampled to 48 kHz.
- `speech_noise.wav` — speech + white/pink noise at 5 dB SNR.
- `speech_music.wav` — stereo, speech + a synthetic music bed (chord of sines + simple drum pulses) at −6 dB relative.
- `speech_silences.wav` — speech, 3.0 s silence, speech, 5.0 s silence, speech; record exact silence intervals.
- `silence.wav` — 5 s digital silence.
- `clip.mp4` — 10 s: ffmpeg `testsrc2` 640x360 25 fps H.264 + AAC audio from `speech_music.wav` (looped/padded to 10 s).
- `تسجيل اختبار 1.wav` — copy of `speech_noise.wav`.
- [ ] Tests (`tests/test_fixtures.py`, session fixture runs `make_fixtures.main()` if `fixtures.json` missing):
```python
def test_all_fixture_files_exist(): ...  # every file in fixtures.json exists
def test_arabic_name_readable():
    info = sf.info(FIXTURES_DIR / "تسجيل اختبار 1.wav"); assert info.samplerate == 48000
def test_arabic_name_ffprobe():  # ffprobe via find_ffprobe() with args list returns duration > 0
def test_silence_intervals_recorded():
    meta = fixture("speech_silences.wav"); assert [round(b-a,1) for a,b in meta["silence_intervals"]] == [3.0, 5.0]
def test_clip_mp4_has_video_and_audio():  # ffprobe streams: 1 video h264, 1 audio
def test_make_fixtures_idempotent(): make_fixtures.main(); make_fixtures.main()
```
- [ ] `uv run pytest -v` → PASS.

### Task 4: Feasibility script + report
**Files:** Create `scripts/feasibility.py`, `docs/FEASIBILITY.md`; output `docs/feasibility_results.json`.

**Interfaces — Produces:** `scripts/feasibility.py` with `CHECKS: list[Check]`, each `Check(name, run_cpu: Callable, run_gpu: Callable | None)`; result rows `{name, device, ok, seconds, peak_vram_mb, note}`. Calls `configure_model_env()` then `setup_cuda_dlls()` first. Each run in try/except, GPU failures don't stop CPU checks. Each engine is cleaned up after use (torch `empty_cache`, delete ONNX sessions / CTranslate2 models). Prints a table, writes JSON, exit code 0 iff every CPU check ok.

Checks (input fixture → what must be true):
- `ffmpeg`: version + `has_encoder("h264_nvenc")` noted (not required ok).
- `torch_cuda`: `torch.cuda.is_available()`, matmul on GPU; note device name.
- `deepfilternet3`: enhance `speech_noise.wav` (feed soundfile arrays; avoid `df.io` loaders; shim `torchaudio.backend.common` only if import fails) → output same length ±1%, RMS of output during a speech-free region lower than input.
- `demucs_htdemucs` via audio-separator (`model_file_dir=model_dir("htdemucs")`, segment 7 → retry 4 on OOM) on `speech_music.wav` → vocals + instrumental stems produced.
- `mdx_kim_vocal_2` via audio-separator (`segment_size=256, batch_size=1`) → vocals stem produced; note provider used (CUDAExecutionProvider vs CPU).
- `silero_vad` on `speech_silences.wav` (16 kHz mono copy) → detected gaps overlap recorded silence intervals (each within ±0.3 s).
- `pedalboard_chain`: HPF 80 Hz → PeakFilter presence +3 dB @ 4 kHz → Compressor on `speech.wav` → finite output, same length.
- `ffmpeg_deesser_loudnorm`: `deesser` then 2-pass `loudnorm` to −16 LUFS on `speech.wav`; measured integrated loudness within ±1 LU.
- `faster_whisper_small`: `speech.wav`, CPU `int8`, GPU `int8_float32` (check supported compute types first) → transcript contains "quick" and "fox" (case-insensitive).
- [ ] Run `uv run python scripts/feasibility.py` (first run downloads models into `models/`). Expected: exits 0.
- [ ] Write `docs/FEASIBILITY.md` from the real JSON: table (check × CPU/GPU: ok, seconds, peak VRAM), exact installed versions (`uv pip list` relevant lines), workarounds applied, anything that failed and why, recommendations for M2+ (e.g. which separator engine to default to on GPU).
- [ ] `uv run pytest -v` → all PASS.

## Final report (your last message)
Files created, commands run with outcomes, feasibility table, deviations from pins and why, open problems.
