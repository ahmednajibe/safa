### A. `subprocess.run([shutil.which("npm"), "run", "build"])` on Windows (Python 3.11)

- **Correctness & safety:** Yes. With `shell=False` (default), `CreateProcessW` detects `.cmd`/`.bat` on the resolved `npm` path and invokes `cmd.exe /c` automatically. Because `["run", "build"]` are hardcoded constants and `cwd` is passed via `cwd=` (`backend/launcher.py:550-553`), CVE-2024-24576-style argument injection via `cmd.exe` metacharacters cannot occur from args.
- **Better option?** Keeping `shutil.which("npm")` is preferable to hardcoding `"npm.cmd"`: `backend/launcher.py` is cross-platform (Windows/macOS/Linux), and some Windows managers (e.g., Volta) expose `npm.exe` rather than `npm.cmd`.
- **fnm shims:** Works. `fnm` prepends its multishell directory (symlink/junction to the active Node install containing `npm.cmd`) to `PATH`, so `shutil.which("npm")` resolves to `...\npm.cmd`. (Caveat: if `PATHEXT` were customized to include `.PS1` before `.CMD`, `which()` could return `npm.ps1`, which `CreateProcessW` cannot execute.)

---

### B. Same-class bugs / `which`-vs-exec mismatches across the repo

No other `.cmd`/`.bat` (`npm`/`npx`/`yarn`/`pnpm`/`code`) invocations via Python `subprocess` or PowerShell `Start-Process` exist:
- **PowerShell build scripts** (`scripts/setup.ps1:77-82`, `scripts/package.ps1:105-110`, `scripts/build-installer.ps1:108`) invoke `npm` directly in PowerShell (which resolves `npm.cmd` via `PATHEXT`). Note `scripts/build-installer.ps1:108` omits the `Get-Command npm` pre-check that `scripts/package.ps1:105` has.
- **`uv` mismatch in `backend/launcher.py`:** `backend/launcher.py:531` checks `shutil.which("uv")`, then `backend/launcher.py:537` (and `backend/launcher.py:372` without a `which` check) runs bare `"uv"` instead of the resolved path. `uv` is normally `uv.exe` (so `CreateProcessW` finds it), but a `.cmd`/`.bat` shim would fail with the same `WinError 2`. By contrast, `packaging/pypi/safa_bootstrap/cli.py:155-160,233-280` and `backend/core/ffmpeg.py:43-45,59,70` pass the resolved path consistently.
- Other bare `subprocess` commands (`powershell`, `taskkill`, `nvidia-smi`, `explorer.exe`, `open`, `xdg-open` in `backend/launcher.py:363,420`, `backend/core/gpu.py:118`, `backend/core/shortcut.py:194`, `backend/core/output_dir.py:143-148`) are native `.exe`/POSIX binaries.

---

### C. Skipping `*.test.*` in `stale_build`

- Skipping `".test." in f.name` (`backend/launcher.py:251-252`) is the right pragmatic scope.
- **Do NOT exclude `*.d.ts`:** `npm run build` runs `tsc --noEmit && vite build` (`frontend/package.json:8`) over all of `src` (`frontend/tsconfig.json:20`), so `frontend/src/types/file-system-access.d.ts` affects build success.
- **Other exclusions (`__snapshots__`, `*.spec.*`, setup files):** None exist under `frontend/src` today — adding filters for nonexistent conventions would be over-engineering.
- **Minor drift:** `scripts/launcher-lib.ps1:83-85` (`Test-StaleBuild`, used by packaging scripts) does not skip `.test.` yet.

---

### D. Bug 2 (`HistoryDrawer` save-again fix) — races, UX holes, and simplification

1. **Double-click / picker-open race:** `saves[key]` is only populated inside `onPct` (`frontend/src/components/HistoryDrawer.tsx:51-53`), which doesn't fire until the first stream chunk arrives (`frontend/src/lib/save.ts:29`). While `showSaveFilePicker` is open (`save.ts:74`) or `fetchStream` is connecting (`save.ts:86`), the `"Save again"` button stays active; a second click triggers a concurrent `showSaveFilePicker` (throwing `error` and overwriting row state) or parallel writes. Setting `saves[key] = { pct: 0 }` synchronously before `await saveJobFileAs(...)` avoids this without breaking the user-gesture rule.
2. **State lost on drawer close / unmount:** `frontend/src/App.tsx:146-152` unmounts `HistoryDrawer` whenever `ui.historyOpen` becomes `false` (e.g., clicking a row, `×`, or `Escape`). Closing mid-save drops progress and `"Saved"` status on reopen, while `HistoryDrawer.tsx:217-223` still renders a `drawer-foot` tied only to `store.job.saving` (active job).
3. **Dock vs. drawer divergence:** Saving the active job from the drawer doesn't disable Dock save buttons (`frontend/src/components/Dock.tsx:80`) or update `job.saved`, and vice versa.
4. **Simpler alternative:** Key `saving` / `saved` / `saveError` in `store` (`frontend/src/state/reducers.ts:291-311`) by `jobId` (or `${jobId}/${name}`) and reuse `saveOne` in `frontend/src/actions.ts:171-188` across both `Dock` and `HistoryDrawer`. That gives one source of truth and survives drawer close/reopen.
