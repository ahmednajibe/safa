# Design review (read-only, do NOT edit files). Repo F:\sound_system — Safa (FastAPI on 127.0.0.1 + React SPA, Windows-first, also macOS/Linux, also a Docker build with no GUI).

## Problem (verified today with logs)
"Save as…" uses the browser File System Access API (`frontend/src/lib/save.ts` → `window.showSaveFilePicker`).
On the user's Chrome 157 / Windows 11 the native Save window opens, the user picks Desktop and presses Save,
and ~1.9 s later the promise rejects with `AbortError: The user aborted a request.` — no file, no error shown.
Chrome site settings / policies don't block file editing. Root cause inside Chrome is unknown. Also
FS Access does not exist in Firefox (falls back to a plain download).

## Proposed design: server-side Save As
Safa's server runs on the user's own PC and already opens a native **folder** picker in a short-lived
subprocess (`backend/core/output_dir.py`: tkinter `askdirectory` via `sys.executable -c`, DPI-aware, topmost,
120 s timeout, single-flight) and already has an atomic copy (`_auto_copy`, `_claim_target`).
New:
1. `POST /api/jobs/{job_id}/files/{name}/save-as` → server opens a native **Save As** dialog (tkinter
   `asksaveasfilename`, initialfile = output name, default extension + filetype from the output, topmost,
   DPI-aware, `confirmoverwrite` on) in a subprocess; on a chosen path it copies the job output there
   atomically (tmp in the same folder + `os.replace`), records `job_files.saved_path` (so "Show in folder"
   works), returns `{status: "saved", path}`; `{status: "cancelled"}` on cancel/timeout;
   503 `picker_unavailable` when no GUI (Docker/headless) → the frontend falls back to a plain browser download.
2. Frontend `saveOne`: call that endpoint (one at a time, button shows "Saving…" while the dialog is open),
   then "Saved" + "Show in folder", or the error text. Same for the History drawer (job-keyed store state).
   "Save all to folder…" → one server folder dialog + copy all (reuse askdirectory).
3. Validation of the chosen path: absolute, parent dir exists & writable, not inside the repo / app DATA_DIR /
   MODELS_DIR (reuse `validate_output_dir` rules on the parent), extension forced/kept.

## Questions
A. Is this the right fix vs. trying to debug Chrome's AbortError (e.g. `startIn`, removing `id`, `types`)?
   Any known Chrome 1xx bug where showSaveFilePicker rejects AbortError after the user picks a file on
   Windows (OneDrive-redirected Desktop `C:\Users\X\OneDrive\Desktop`)?
B. Security: the endpoint writes anywhere the user picks — risk if a malicious web page POSTs to it?
   (Server has a Host allow-list + same-origin checks; check `backend/main.py` / middleware.) Is a native
   dialog the user must click enough of a guard? Anything else needed (CSRF/Origin check on this POST)?
C. tkinter `asksaveasfilename` pitfalls on Windows: window appearing behind the browser, DPI, Arabic paths,
   long paths, `.wav` default extension when the user types a name without one, macOS (Tk from
   python.org/uv standalone builds — is Tk even present in the uv standalone CPython on Windows/macOS/Linux?),
   Linux without a display.
D. How should the HTTP request behave while the dialog is open (up to 120 s): block the request (sync def
   in threadpool) vs. start-then-poll? Server threadpool limits? Browser fetch timeouts?
E. Anything simpler that is equally robust?

Answer in Markdown, < 700 words, with file:line references where relevant.
