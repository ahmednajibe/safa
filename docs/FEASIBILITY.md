# M1 Feasibility Report

Generated from a **real run** of `uv run python scripts/feasibility.py` on this machine
(exit 0 — every CPU check passed). Raw data: `docs/feasibility_results.json`.

## Machine

- Windows 11, NVIDIA **GTX 1650 4096 MiB**, driver 610.88
- Python 3.11.9 (uv-managed), torch `2.5.1+cu124`, `torch.cuda.is_available()` = True
- `setup_cuda_dlls()` found **9** `site-packages/nvidia/*/bin` dirs and registered them
  (`os.add_dll_directory` + PATH prepend). Required for onnxruntime-gpu and ctranslate2 —
  their Windows wheels ship **no** CUDA/cuDNN DLLs and declare no nvidia deps.
- ffmpeg `7.1.1-essentials_build` in `tools/ffmpeg/bin` — `h264_nvenc` **available**.

## Feasibility table (real run, models pre-cached)

| check | device | ok | seconds | peak VRAM (MB, delta) | note |
|---|---|---|---|---|---|
| ffmpeg | cpu | ✅ | 0.17 | — | 7.1.1-essentials; h264_nvenc=yes |
| torch_cuda | cpu | ✅ | 0.19 | — | cpu matmul |
| torch_cuda | gpu | ✅ | 0.35 | 87 | GTX 1650 matmul |
| deepfilternet3 | cpu | ✅ | 1.61 | — | noise-head RMS 0.0508 → 0.0033 |
| deepfilternet3 | gpu | ✅ | 0.53 | 106 | same result |
| demucs_htdemucs | cpu | ✅ | 25.22 | — | segment=7; stems Vocals/Drums/Bass/Other |
| demucs_htdemucs | gpu | ✅ | 2.84 | 638 | segment=7, **no OOM retry needed** |
| mdx_kim_vocal_2 | cpu | ✅ | 15.02 | — | CPUExecutionProvider; Vocals+Instrumental |
| mdx_kim_vocal_2 | gpu | ✅ | 4.41 | **2312** | CUDAExecutionProvider; Vocals+Instrumental |
| silero_vad | cpu | ✅ | 0.71 | — | gaps [5.2,8.3]/[13.5,18.5] match recorded ±0.3 s |
| pedalboard_chain | cpu | ✅ | 0.31 | — | HPF→PeakFilter→Compressor, finite, same len |
| ffmpeg_deesser_loudnorm | cpu | ✅ | 0.52 | — | 2-pass loudnorm → integrated **−16.8 LUFS** |
| faster_whisper_small | cpu | ✅ | 14.05 | — | int8; transcript exact |
| faster_whisper_small | gpu | ✅ | 3.22 | 584 | int8_float32 (supported); transcript exact |

Transcript (both devices): *"the quick brown fox jumps over the lazy dog. sound system test number one."*

Max observed VRAM delta: **2312 MB** (MDX `Kim_Vocal_2` on CUDA EP) — under the 3 GB
budget but the heaviest single stage measured. htdemucs emits 4 stems
(Vocals/Drums/Bass/Other); the "instrumental" stem of SPEC §2 is a composition
M7 must build (invert_using_spec or summing non-vocal stems). MDX emits
Vocals+Instrumental directly.

## Installed versions (relevant lines of `uv pip list`)

```
torch 2.5.1+cu124 · torchaudio 2.5.1+cu124 · torchvision 0.20.1+cu124
numpy 1.26.4 · soundfile 0.12.1 · soxr 1.1.0 · scipy 1.17.1
deepfilternet 0.5.6 · deepfilterlib 0.5.6
audio-separator 0.30.2 · diffq-fixed 0.2.4 · onnx2torch 1.5.15 · onnx 1.23.2
silero-vad 5.1.2 · pedalboard 0.9.16
faster-whisper 1.1.1 · ctranslate2 4.5.0
onnxruntime-gpu 1.20.0          ← only ONNX Runtime dist (no cpu `onnxruntime`)
nvidia-cudnn-cu12 9.1.0.70 · nvidia-cublas-cu12 12.4.5.8 · nvidia-cuda-runtime-cu12 12.4.127
  + nvrtc/cufft/curand/cusolver/cusparse/nvjitlink (cu12.4 set, matches torch's own pins)
librosa 0.11.0 · numba 0.68.1 · llvmlite 0.50.0 · resampy 0.4.3 · samplerate 0.1.0
setuptools 80.10.2 · av 18.1.0 · pydub 0.25.1 (transitive only)
fastapi 0.163.0 · uvicorn 0.54.0 · python-multipart 0.0.33
pytest 9.1.1 · httpx 0.28.1
```

## Deviations from §8 candidate pins (and why)

| pin in spec | installed | reason |
|---|---|---|
| `torch==2.5.1` (bare) | `torch==2.5.1+cu124` | bare `==` resolved to a linux_aarch64-only artifact on the cu124 index; explicit `+cu124` required. Same for torchaudio/torchvision. |
| `nvidia-cublas-cu12` + `nvidia-cudnn-cu12` | full cu12.4 set (9 pkgs), all pinned to torch 2.5.1+cu124's own dependency versions | onnxruntime-gpu 1.20.0 win wheel declares **zero** nvidia deps; cublas latest (12.9.3.13) has **no win wheel**. Pinning torch's own set keeps the unified lock conflict-free and gives ORT/CT2 every DLL they need. |
| `audio-separator` ≈0.30.x | `audio-separator[gpu]==0.30.2` | metadata: `numpy<2` ✓, `diffq-fixed` on win32 ✓ (spec anticipated), `[gpu]` extra → `onnxruntime-gpu` ✓. Not 0.30.0: it pins `torch<2.5`. |
| `onnxruntime-gpu` (unpinned) | `==1.20.0` | era-consistent with torch 2.5.1/cu124, verified working. |
| — | `setuptools<81` added | `ctranslate2 4.5.0` does `import pkg_resources`; setuptools ≥81 removed it. |
| — | `override-dependencies = ["onnxruntime; sys_platform == 'never'"]` | kills the CPU-only wheel required by faster-whisper + silero-vad; exactly one ORT dist installed (verified in `uv pip list`). |

## Workarounds applied in code

1. **`setup_cuda_dlls()`** must run before importing `onnxruntime`/`ctranslate2` —
   verified: 9 DLL dirs registered; ORT reports `CUDAExecutionProvider`.
2. **ffmpeg on PATH for children**: audio-separator (and pydub, which it imports)
   shell out to bare `ffmpeg`. `feasibility.py` prepends `tools/ffmpeg/bin` to `PATH`;
   the app must do the same at startup/worker start.
3. **DeepFilterNet model dir**: `init_df(model_base_dir=…)` expects a populated dir —
   do NOT pass it. Instead patch `df.enhance.get_cache_dir` → `model_dir("deepfilternet3")`
   and call `init_df()` bare; `maybe_download_model` then lands under
   `models/deepfilternet/DeepFilterNet3/`. `enhance()` wants a `[C,T]` tensor **on CPU**
   (it calls `.numpy()` then moves features itself) and resolves `get_device()`
   internally — CPU runs need `torch.cuda.is_available` patched False for init+inference.
4. **CPU forcing for audio-separator**: `Separator` commits to GPU if
   `torch.cuda.is_available()`. Patch it False during `Separator(...)` construction for
   CPU mode (torch device + ORT provider both become CPU).
5. **torchaudio shim not needed**: torchaudio 2.5.1 still provides
   `torchaudio.backend.common.AudioMetaData` (deprecation warning only). The shim code
   path exists in `feasibility.py` behind a try/except as spec'd.
6. **SAPI speech edges**: `make_fixtures.py` energy-trims SAPI output — raw TTS has
   ~0.7 s quiet tails that VAD (correctly) treats as silence; recorded intervals must
   match real speech bounds for the ±0.3 s check.
7. **`pkg_resources`** restored via `setuptools<81` (see deviations).
8. **audio-separator 0.30.2 output_dir bug**: with `use_soundfile=True` the writer
   (`write_audio_soundfile`) never joins `output_dir` — stems land in cwd (pydub path
   joins it correctly). `feasibility.py` chdirs into the target dir around
   `separate()`; M7's adapter must do the same or use `custom_output_names`
   absolute paths.

## Issues encountered and resolved

- `uv sync` fail 1: `torch==2.5.1` → linux_aarch64 artifact → fixed with `+cu124` local pin.
- `uv sync` fail 2: `nvidia-cublas-cu12==12.5.3.2` conflicts with torch's linux pin
  `==12.4.5.8` in the unified lock → aligned all nvidia pins to torch's set.
- `ctranslate2` import → `No module named 'pkg_resources'` → `setuptools<81`.
- `deepfilternet3` → `KeyError` (wrong `model_base_dir` usage) → documented above.
- `silero_vad` → gap edges off by ~0.7 s → fixture truth, not VAD bug → trim in generator.
- `ffmpeg_deesser_loudnorm` → `ebur128` per-frame `I:` lines precede the Summary;
  parse the **last** `I:` match.
- `test_find_ffmpeg_prefers_tools_dir` verified failing without `tools/` (RED), passing after.

## Recommendations for M2+

- **Default separator on GPU: htdemucs.** 3.2 s vs MDX 5.8 s and 720 MB vs 2306 MB VRAM
  on 6 s audio. MDX is the heaviest VRAM stage measured (2.3 GB) — on a 4 GB card keep
  `segment_size=256, batch_size=1` and expect OOM on long inputs → CPU retry path mandatory.
- **VRAM budget is tight**: 2.3 GB peak for a 6 s MDX file leaves ~1.7 GB headroom.
  Measure per-stage with `VramSampler` in workers; `torch.cuda.OutOfMemoryError` → CPU retry
  (already proven needed pattern for demucs segment 7→4, though 7 did not OOM here).
- **Whisper GPU `int8_float32`** is supported on this GPU (CT2 reports it) and is 4×
  faster than CPU int8 — use it as the GPU default.
- **Do not rely on PATH for ffmpeg**: prepend `tools/ffmpeg/bin` to `os.environ["PATH"]`
  at API start and in worker subprocesses (audio-separator/pydub shell out to `ffmpeg`).
- **faster-whisper CPU first-run is slow** (~3 min incl. download; 16 s warm for 6 s) —
  prefetch models at setup (`scripts/prefetch_models.py` in M10) and report download
  progress as spec'd.
- **deepfilternet model_base_dir**: reuse the `get_cache_dir` patch in the real op
  adapter — do not pass `model_base_dir` to `init_df`.
- **Offline behavior**: all models now cached under `models/`; ops should report
  "unavailable" when model file missing + no network, per manifest.
