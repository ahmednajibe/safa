### 1. `showSaveFilePicker` as Default ([SPEC.md](file:///F:/sound_system/docs/SPEC.md#L115-L120))
* **Right default, with one crucial limitation:** `FileSystemFileHandle` never exposes the chosen filesystem path to JavaScript. After a manual save via `showSaveFilePicker`, the backend doesn't know where the user saved the file—so **"Show in folder"** (`explorer.exe /select`) cannot open the user-picked folder (it only works for auto-saved files or the internal `data/jobs/<id>/output` copy).
* **User gesture:** Call `showSaveFilePicker()` **before** `await fetch(...)`; any `await` before opening the picker loses transient activation and throws `SecurityError`.
* **Large video streaming & progress:** Never buffer with `res.blob()`. Pipe `res.body.pipeTo(writable)` so multi-GB MP4s stream directly to disk (`.crswap` temp file) with flat RAM. Because Chromium hides the download shelf for `FileSystemWritableFileStream`, pipe through a byte-counting `TransformStream` against `size_bytes` and show tabular progress (`Saving 45%…`) on the button.
* **Cancelling & errors:** Ignore `err.name === "AbortError"` silently; call `writable.abort()` on network/stream errors so partial `.crswap` files are removed.
* **Filters & Arabic names:** Pass `id: "ss-" + kind` (so Edge/Chrome remember the last folder per file type), `suggestedName: file.name` (keeps Arabic + spaces from [storage.py](file:///F:/sound_system/backend/media/storage.py#L61-L65); also strip Windows reserved stems `CON/PRN/AUX/NUL/COM1..9`), and explicit single-MIME `types` (Chromium throws `TypeError` on `*/*`).

### 2. Native Tkinter Folder Dialog Risks & Security
* **Window focus / Z-order:** A child subprocess often opens `askdirectory` **behind** the browser window, making the UI look frozen. Set `root.wm_attributes("-topmost", 1)` + `root.withdraw()`, call `SetProcessDpiAwarenessContext` before `Tk()` (fixes blurry Windows 11 scaling), and enforce a backend single-flight lock + 120 s timeout so repeated clicks don't spawn zombie processes.
* **Headless / fallback:** If the server starts without an interactive desktop session, Tk fails or hangs. Add a **text path input** inside the Settings popover alongside `"Change…"` so users can also paste a folder path directly.
* **Security & validation:** Reject paths inside the entire repo root `F:\sound_system` (not just `data/`), drive roots (`C:\`), symlinks (`Path.resolve()`), and cross-origin requests (require `Origin: http://127.0.0.1:<port>` so other browser tabs cannot trigger native dialogs or file writes via CSRF).

### 3. Auto-Copy Behaviour ([worker.py](file:///F:/sound_system/backend/worker.py#L208-L228), [runner.py](file:///F:/sound_system/backend/pipeline/runner.py#L81-L84))
* **Copy, never move:** A/B waveform playback, video preview, and 7-day History re-downloads in [jobs.py](file:///F:/sound_system/backend/api/jobs.py#L324-L344) read from `data/jobs/<id>/output/`.
* **Never auto-save preview jobs:** In [runner.py](file:///F:/sound_system/backend/pipeline/runner.py#L82-L83), `kind == "preview"` writes `*_preview.wav`. Auto-saving previews would spam the user's folder on every 15 s test run.
* **Atomic writes & collisions:** Copy to `<output_dir>/.<name>.<uuid>.tmp` then `os.replace()` into `<stem> (2).<ext>` so crashes or unplugged drives never leave corrupt files.
* **Missing USB / full disk:** Check `free_space_bytes(output_dir)` before copying. If the folder vanished or disk is full, **never fail the job**—complete it using `data/jobs/<id>/output` and emit `warning_code: "auto_save_failed"{path}` so the user can still save manually.

### 4. UI Wording, Placement & Missing Details ([UI_DESIGN.md](file:///F:/sound_system/docs/UI_DESIGN.md#L242-L245))
* **State-aware dock copy:** When `"Always save to"` succeeds, show the `"Saved"` (`"تم الحفظ"`) pill + `"Show in folder"` (`"إظهار في المجلد"`) **immediately** on completion, and label buttons `"Save a copy…"` (`"حفظ نسخة…"`) instead of `"Save MP4…"`.
* **Arabic refinements:** Use `"حفظ باسم…"` for Save As, `"الحفظ التلقائي في"` for Always save to, and wrap paths in `<bdi dir="ltr">`.
* **Missing:** Add a `"Save all to folder…"` (`"حفظ الكل في مجلد…"`) action when a job outputs multiple files (video + audio + SRT + stems).

### 5. Concrete Changes Recommended
1. **Skip auto-copy when `job.kind == "preview"`**; add `saved_path` column to `job_files`.
2. **Make auto-copy non-fatal:** copy via `.tmp` + `os.replace()`; on `OSError`/low space, add warning `auto_save_failed` instead of failing the job.
3. **Only show `"Show in folder"`** when `saved_path` exists (or reveal `data/jobs/<id>/output` explicitly), since `showSaveFilePicker` hides the destination path.
4. **Harden the picker:** add `Origin` check, repo-root blocklist (`F:\sound_system`), single-flight lock, DPI + topmost flags, and a paste-path input fallback.
