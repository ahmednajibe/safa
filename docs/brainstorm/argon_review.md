### 1. Technical Pitfalls (Windows + GTX 1650 4 GB + Python 3.11)

- **DeepFilterNet vs. `torchaudio >= 2.1`**: `deepfilternet==0.5.6` imports `torchaudio.backend.common.AudioMetaData` (removed in `torchaudio 2.1`). Inject a 5-line `sys.modules["torchaudio.backend.common"]` shim before importing `df`, and feed `soundfile` tensors directly instead of using `df`'s `torchaudio.info` loader.
- **NumPy 2.x breakage**: `DeepFilterNet` and several `audio-separator` sub-deps break on `numpy >= 2.0`. Pin `numpy<2.0`.
- **`onnxruntime-gpu` & `faster-whisper` (`ctranslate2`) DLL hell**: On Windows, `onnxruntime-gpu>=1.19` and `ctranslate2>=4.5` require CUDA 12 + **cuDNN 9** DLLs that aren't bundled in their wheels. Install `nvidia-cublas-cu12` and `nvidia-cudnn-cu12` in `uv` and call `os.add_dll_directory()` / prepend `site-packages/nvidia/*/bin` to `PATH` at worker startup.
- **`audio-separator` on 4 GB VRAM**: Default `htdemucs` segment lengths can spike >3.5 GB VRAM on stereo audio. Force small chunk sizes (`segment_size=7` for Demucs; `segment_size=256, batch_size=1` for MDX). Ensure `tools/ffmpeg/bin` is on `PATH` before importing `audio_separator`.
- **`silero-vad` packaging**: Use the PyPI `silero-vad` package (not `torch.hub.load`, which breaks offline). It strictly requires **16 kHz (or 8 kHz) mono**—resample 48k→16k mono only for timestamp detection, then apply timestamps to the 48k stereo stream.
- **GTX 1650 (Turing CC 7.5)**: Lacks FP16 Tensor Cores (`float16` is slow/unsupported in CTranslate2), so `int8_float32` (GPU) and `int8` (CPU) in [SPEC.md](file:///F:/sound_system/docs/SPEC.md#L27) are spot-on.

### 2. Recommended Version Pins (`pyproject.toml`)

- **PyTorch (`https://download.pytorch.org/whl/cu121`)**: `torch==2.4.1+cu121`, `torchaudio==2.4.1+cu121`
- **CUDA Runtime Wheels**: `nvidia-cublas-cu12==12.1.3.1`, `nvidia-cudnn-cu12==9.1.0.70`
- **Core / DSP**: `numpy==1.26.4`, `soundfile==0.12.1`, `soxr==0.5.0.post1`, `pedalboard==0.9.16`
- **ML Libs**: `deepfilternet==0.5.6`, `audio-separator==0.28.5`, `onnxruntime-gpu==1.19.2`, `silero-vad==5.1.2`, `faster-whisper==1.1.0` (`ctranslate2==4.5.0`)

### 3. Spec Gaps & Oversized Milestones

- **FFmpeg Filtergraph CLI Overflow ([SPEC.md](file:///F:/sound_system/docs/SPEC.md#L37))**: A 30-min lecture after `silence` cutting can have 500+ keep-intervals. Passing inline `-filter_complex` strings hits Windows' 32 KB CLI limit and OOMs ffmpeg `concat`. **Fix**: Write filtergraphs to a temp file and use `-filter_complex_script` (and clarify how `speed` scales video `setpts` alongside `trim`/`silence` intervals).
- **Streaming File I/O ([SPEC.md](file:///F:/sound_system/docs/SPEC.md#L35))**: Mandate block/chunked WAV streaming (`soundfile.blocks`) for `pedalboard` and `DeepFilterNet` so a 60-min 48 kHz float32 stereo WAV (~1.4 GB per array) doesn't exhaust 16 GB RAM.
- **Split M4 ([PLAN.md](file:///F:/sound_system/docs/PLAN.md#L27-L30))**: 8 DSP ops + conflict engine + keep-interval timeline math + NVENC video filtergraph is too large. Split into **M4a** (audio DSP ops + ordering/conflicts) and **M4b** (timeline keep-intervals + video re-encode/sync).
- **Split M9 ([PLAN.md](file:///F:/sound_system/docs/PLAN.md#L48-L50))**: Split into **M9a** (upload, inspector, op cards, job progress, downloads/history) and **M9b** (Wavesurfer preview region + synced video/audio A/B player).
