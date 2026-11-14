- **1. Device model (`auto / gpu / cpu` + per-step `run_on`)**
  - **Redundant `auto` vs `gpu`**: In [SPEC.md](file:///F:/sound_system/docs/SPEC.md#L90-L93), `auto` and `gpu` have identical runtime behavior when a GPU is present, and `gpu` is disabled when absent. Offering both creates a false choice.
  - **Missing `resolve_device` & engine cases**:
    - *Param-dependent CPU ops*: In [registry.py](file:///F:/sound_system/backend/operations/registry.py#L127-L135), `denoise` is `gpu-optional`, but `engine="classic"` (`afftdn`) is CPU-only. `resolve_device()` in [2026-11-15-m3a-media-storage.md](file:///F:/sound_system/docs/superpowers/plans/2026-11-15-m3a-media-storage.md#L54) must resolve `denoise` + `classic` to `"cpu"` so stepper badges and SSE don't falsely report `"gpu"`.
    - *Video encoding*: [SPEC.md §3](file:///F:/sound_system/docs/SPEC.md#L37) uses `h264_nvenc` for timeline video cuts, while [§9](file:///F:/sound_system/docs/SPEC.md#L96) says ffmpeg ignores device choice. Choosing `cpu` ("keeps the graphics card free") must also force `libx264` instead of `h264_nvenc`.
    - *Engine differences & health*: Per [FEASIBILITY.md](file:///F:/sound_system/docs/FEASIBILITY.md#L82-L87), forcing CPU in the worker requires engine-specific handling—setting `CUDA_VISIBLE_DEVICES=""` in the worker env (or patching `torch.cuda.is_available` for DFN3/Separator, `CPUExecutionProvider` for ONNX, `device="cpu"` for CTranslate2)—and catching engine-specific OOMs (`torch.cuda.OutOfMemoryError`, ORT `Fail`/`RuntimeException`, CT2 `RuntimeError`). Also fix [health.py](file:///F:/sound_system/backend/api/health.py#L16) to check `caps.cuda_dlls` before advertising `"gpu"`.
    - *Running jobs & persistence*: Snapshot `device` on the job at submission (`POST /api/jobs`); persist the user's dock selection in `localStorage`.

- **2. Retrying on CPU after OOM when GPU was chosen**
  - **Do not fail or prompt mid-job**: Failing discards prior stages on 30–60 min files, and prompts break unattended runs.
  - **Shrink GPU chunks first, then fall back to CPU**: Retry Demucs (`segment=7 → 4`) on GPU first ([SPEC.md §8](file:///F:/sound_system/docs/SPEC.md#L82)); if OOM persists (e.g. MDX at 2.3 GB VRAM), fall back to CPU with the warning pill `"GPU memory full — running on CPU"`. Because OOM fallback is always desirable here, `Auto` and `GPU` should be unified.

- **3. UI control in the dock & per-step override**
  - **Drop hover-explained 3-way toggle**: A 3-way `Auto · GPU · CPU` control needing hover-only helper sentences ([UI_DESIGN.md](file:///F:/sound_system/docs/UI_DESIGN.md#L118)) breaks `design-taste-frontend` (*"redesign the control instead of adding the sentence"*) and hides meaning from touch/keyboard users.
  - **Remove per-step `run_on`**: Only 3 v1 ops use GPU (`separate`, `denoise` AI, `subtitles`), and stages run sequentially with VRAM freed between them ([SPEC.md §4](file:///F:/sound_system/docs/SPEC.md#L45)). Per-step overrides don't reduce peak VRAM and clutter cards.

- **4. Showing estimated time per device**
  - **Show coarse pre-run buckets, never fake second-by-second ETAs**: Using [FEASIBILITY.md](file:///F:/sound_system/docs/FEASIBILITY.md#L17-L33) real-time multipliers (6 s fixture: Demucs ~0.5× GPU vs ~4× CPU; MDX ~0.6× vs ~2.5×; Whisper ~0.5× vs ~2.3×; DFN3 ~0.1× vs ~0.25×), multiply `probe.duration_s` by selected ops' factors and show rounded buckets (`tabular-nums`): e.g. `"About 1 min"` vs `"About 7 min"` (or `"~5× slower on CPU"` before upload).

- **5. Concrete recommendations**
  - **UI**: Replace `Auto · GPU · CPU` in the dock with a 2-option segmented control **`Fast (GPU)` | `Quiet (CPU)`** (default `Fast (GPU)` when available; hidden when CPU-only, since the top bar already says `CPU only`). Show a single `tabular-nums` subline beside **Process**: `"Uses GTX 1650 · about 2 min (falls back to CPU if memory fills)"` vs `"Uses processor · about 10 min, keeps GPU free"`.
  - **Backend**: Drop `run_on` per-op param; keep job `device: "auto" | "gpu" | "cpu"` in API (`auto`/`gpu` aliases); make `resolve_device` return `"cpu"` for `denoise` + `classic` and force `libx264` when `device == "cpu"`.
