- **Hardware correction:** “No Tensor Cores → fp32/int8 only” is incorrect. GTX 1650 supports FP16; performance depends on kernels. Keep FP32 as the conservative default and query CTranslate2’s supported compute types.

- **DeepFilterNet:** `deepfilternet==0.5.6` imports `torchaudio.backend.common.AudioMetaData`. TorchAudio 2.5.1 retains the compatibility alias; 2.9 removes it. Avoid an unnecessary shim with the proposed pins. Use SoundFile I/O: bundled static `ffmpeg.exe` does not supply TorchAudio’s shared FFmpeg libraries. [Issue](https://github.com/Rikorose/DeepFilterNet/issues/662)

- **Candidate core pins—not machine-validated:** `torch==2.5.1`, `torchaudio==2.5.1`, `torchvision==0.20.1`, all from `https://download.pytorch.org/whl/cu124`; `numpy==1.26.4`, `packaging==23.2`, `soundfile==0.12.1`. TorchVision is pulled indirectly through `onnx2torch`; constrain it too.

- **Candidate ML pins:** `deepfilternet==0.5.6`, `deepfilterlib==0.5.6`, `audio-separator==0.30.2`, `diffq-fixed==0.2.4`, `onnx==1.17.0`, `onnx2torch==1.5.15`, `silero-vad==5.1.2`, `pedalboard==0.9.16`, `faster-whisper==1.1.1`, `ctranslate2==4.5.0`.

- **ONNX/CUDA packaging:** Use `onnxruntime==1.20.1` for CPU or `onnxruntime-gpu==1.20.1` for CUDA 12/cuDNN 9. Silero and faster-whisper require the CPU distribution; adding GPU extras installs overlapping packages. Isolate GPU separation or explicitly resolve that dependency substitution. Expose matching CUDA/cuDNN DLLs inside every worker. [Compatibility matrix](https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html)

- **Separator installation:** Version 0.30.2 selects `diffq-fixed` on Windows; its 0.2.4 release has a Python 3.11 Windows wheel. It also requires `pydub`, contradicting a blanket ban. Avoid unbounded upgrades: current separator metadata requires NumPy ≥2, conflicting with DeepFilterNet’s <2.

- **DSP/optional restoration:** Pedalboard has no built-in de-esser; specify FFmpeg `deesser` or implement one. Resemble’s `--no-deps` does not solve Windows DeepSpeed compatibility; retain a separate feasibility gate.

- **VRAM:** PyTorch’s OOM exception, allocator measurements, and `empty_cache()` do not cover ONNX Runtime or CTranslate2. Add engine-specific fallback and cleanup, total GPU-memory measurement, and explicit Demucs segment limits.

- **Timeline correctness:** Keep-intervals alone cannot represent speed changes and overlapping crossfades. Each 15 ms overlap shortens audio; unchanged video concatenation accumulates drift. Specify composed source/output time mapping, including A/B playhead translation.

- **Acceptance:** Synthetic tones cannot validate speech VAD, transcription, or voice preservation. Add licensed real speech with a clean reference, silence-only cases, and long-video synchronization tests. M1 must exercise actual CUDA inference and the VRAM budget.

- **Milestone sizing:** Split M3 into media/storage and worker lifecycle/SSE; split M4 into DSP and timeline/video; deliver M9 incrementally. Define model filenames, hashes, cache locations, and offline behavior in M1 rather than waiting until M10.
