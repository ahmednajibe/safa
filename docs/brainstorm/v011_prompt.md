# Safa v0.1.1 — design review request (read-only; do NOT edit files)

You are reviewing a plan for a Windows/macOS/Linux desktop app (FastAPI backend +
React UI, Python 3.11 via uv, repo at F:\sound_system). Read the files named
below, then answer the questions. Be concrete: name files/lines, flag risks,
and say what you would do differently. Keep it under ~900 words.

## Context (verified facts)

- `pyproject.toml` has conflicting extras `cpu` and `cu124` (uv `conflicts`).
  Installers ship `uv export --frozen --no-dev --extra <x>` hashed requirement
  files; the Windows online installer (`packaging/windows/inno/online.iss`)
  runs `scripts/install-runtime.ps1`, which does `uv pip install -r
  requirements-<variant>.txt --require-hashes`. Offline installers bundle the
  same runtime. GitHub release assets must be < 2 GiB.
- Windows GPU (cu124) install today: `torch` 4.4 GB installed, `nvidia/`
  (9 nvidia-*-cu12 wheels: cublas, cudnn 9.1.0.70, cuda-runtime, cufft,
  curand, cusparse, cusolver, nvrtc, nvjitlink) 2.7 GB, `onnxruntime`
  (onnxruntime-gpu 1.20.0) 746 MB. Download ≈ 4.5–5 GB.
- On Windows, `torch==2.5.1+cu124`'s own `torch\lib` already contains:
  cublas64_12, cublasLt64_12, cudart64_12, cudnn64_9 (+ cudnn_*64_9),
  cufft64_11, curand64_10, cusolver64_11, cusparse64_12, nvrtc64_120_0,
  nvJitLink_120_0. So on Windows the nvidia-* wheels are duplicates.
  On Linux, torch cu124 itself depends on nvidia-* wheels (no duplication).
- The nvidia-* pins exist (pyproject.toml lines ~28–36, gated
  `extra == 'cu124' and sys_platform != 'darwin'`) so that ctranslate2 4.5.0
  (faster-whisper) and onnxruntime-gpu 1.20.0 (audio-separator MDX models)
  find CUDA DLLs. `backend/core/gpu.py::setup_cuda_dlls()` adds
  `site-packages/nvidia/*/bin` to `os.add_dll_directory` + PATH; it's called
  in `backend/worker.py` and `backend/media/transcribe_child.py` before
  importing onnxruntime/ctranslate2/torch.
- CPU variant on Windows/Linux x64 also installs onnxruntime-gpu (746 MB
  installed) instead of the ~30–50 MB `onnxruntime`. Reason (pyproject
  comment near `override-dependencies`): faster-whisper / silero-vad require
  `onnxruntime`; installing both `onnxruntime` and `onnxruntime-gpu` clobbers
  the shared `onnxruntime/` package dir; uv `override-dependencies` cannot be
  gated by our own extras, so the split is by platform.
- Online installer UX: `online.iss` `RunRuntimeInstall()` runs PowerShell
  hidden with `Exec(..., ewWaitUntilTerminated)` behind a
  `TOutputProgressWizardPage` with `SetProgress(0,0)` — the wizard freezes
  with static text for 20–40 min (no bar, no %, text shown twice). Inno Setup
  version used to build: 6.4.3. uv does not print byte progress when not on
  a TTY.
- GPU offline installer (`packaging/windows/inno/offline-gpu.iss`) is
  DiskSpanning exe + `-1.bin`, `-2.bin`. Its `InitializeSetup` slice check
  only runs if `slicecount.txt` sits next to the exe; normal users download
  only the exe, so setup starts and then shows Inno's "insert disk" dialog.
- The GitHub release page lists 31 files with no guidance.

## Proposed changes

1. Windows cu124: gate the nvidia-* pins to `sys_platform == 'linux'`; on
   Windows make `setup_cuda_dlls()` also add `<site-packages>/torch/lib`
   (or import torch first) so ctranslate2 + onnxruntime-gpu CUDA EP load the
   torch-bundled DLLs. Expected: −2 GB download, −2.7 GB installed; GPU
   offline installer may drop from 3 files to 2.
2. CPU variant: ship the small `onnxruntime` instead of onnxruntime-gpu on
   Windows/Linux x64 — without both packages ever landing in one env.
3. Online installer: real progress — stage text ("Step 2 of 4: Downloading
   PyTorch (GPU)"), a determinate bar from bytes landed in the uv cache dir
   vs a per-variant expected total, "2.1 GB of 5.0 GB — 8.4 MB/s", rough
   smoothed ETA after ~10 s, working Cancel, no frozen window.
4. GPU offline: with no slicecount.txt, still require `-1.bin` (+ any
   contiguous slices) before the wizard starts; name the exact files.
5. Release notes: a bilingual "Which file do I download?" table.

## Questions

A. Change 1: is pointing ctranslate2 4.5.0 and onnxruntime-gpu 1.20.0 at
   `torch\lib` safe on Windows? Version matches (cuDNN 9.1, cuBLAS 12.4)?
   Any DLL the CUDA EP or ctranslate2 needs that torch\lib lacks (e.g.
   cudnn sub-libs, zlibwapi, cufft for onnxruntime)? Import-order or
   `add_dll_directory` pitfalls? How would you verify it on a real GPU
   (GTX 1650, 4 GB) in tests?
B. Change 2: best mechanism given uv conflicts/overrides limits? Options:
   (a) restructure markers/extras so `cpu` gets `onnxruntime` and `cu124`
   gets `onnxruntime-gpu` with the third-party `onnxruntime` requirement
   neutralised only in cu124; (b) post-process the exported
   requirements-cpu.txt (swap onnxruntime-gpu → onnxruntime with locked
   hashes); (c) other. Which is least fragile? Does audio-separator 0.30.2
   (base, non-[gpu]) itself need onnxruntime-gpu?
C. Change 3: in Inno 6.4.3, best way to keep the wizard responsive and show
   progress — `ExecAndLogOutput` with an OnLog callback that parses
   `PROGRESS ...` lines printed by install-runtime.ps1? `Exec` with
   `ewNoWait` + a polling loop on a progress file? Something else? How to
   implement Cancel safely (kill the PowerShell + uv tree)?
D. Anything in this plan that is wrong, risky, or missing? Is there a
   simpler way to get a similar size win?

Write your answer as Markdown to stdout only.
