# Review request (read-only — do NOT edit files). Repo: F:\sound_system (Safa, FastAPI + React, Windows-first).

## Bug 1 — `start.bat` crashed
User ran `start.bat` (dev checkout). Output:
```
== frontend build ==
  File "F:\sound_system\backend\launcher.py", line 543, in cmd_start
    rc = subprocess.run(
FileNotFoundError: [WinError 2] The system cannot find the file specified
```
Cause found: `backend/launcher.py::cmd_start` checks `shutil.which("npm")` (finds `npm.cmd` via
PATHEXT) but then runs `subprocess.run(["npm", "run", "build"])` — CreateProcess does not apply
PATHEXT, so a bare "npm" fails. The rebuild path only ran because `stale_build()` saw a NEW
`frontend/src/components/HistoryDrawer.test.tsx` newer than `frontend/dist/index.html`.

Fix applied (read `backend/launcher.py` around `stale_build` and `cmd_start`):
1. `npm = shutil.which("npm")` then `subprocess.run([npm, "run", "build"], ...)`.
2. `stale_build` skips files whose name contains `.test.`.

## Bug 2 — "Save again" in the History drawer showed nothing
`frontend/src/components/HistoryDrawer.tsx` called `saveOne(store, jobId, f)` whose progress/error state
(`store.job.saving`, `store.job.saveError`) is only rendered by `Dock.tsx` for the ACTIVE job, so a save
started from the drawer showed no progress, no "Saved", no error. Fix: the drawer keeps its own per-row
state and calls `lib/save.ts` `saveJobFileAs` directly (picker first await), rendering "Saving N%", "Saved"
or the error. Read both files.

## Questions
A. Is running the full `shutil.which` path of `npm.cmd` via `subprocess.run([path, ...])` (no shell) safe and
   correct on Windows with Python 3.11 (CVE-2024-24576-style batch-arg concerns? args here are constant)?
   Any better option (e.g. `npm.cmd` explicitly, `shell=False`)? Does it work when npm comes from fnm shims?
B. Search the whole repo (backend/, scripts/, packaging/, frontend build scripts) for the SAME class of bug:
   subprocess/Start-Process calls of a bare command name that is really a `.cmd`/`.bat` on Windows
   (npm, npx, yarn, pnpm, code, etc.), and for other places where a which()-check and the exec use different
   names. List file:line.
C. Is skipping `*.test.*` in `stale_build` correct, or are there other non-bundle files under
   frontend/src (e.g. `*.d.ts`, test setup, `__snapshots__`) worth excluding — or is that over-engineering?
D. Bug 2: any race or UX hole in the drawer fix (e.g. two saves at once, drawer closed mid-save, state
   lost on refresh, dock and drawer saving the same file)? Anything simpler?

Answer in Markdown, under ~600 words, with file:line references.
