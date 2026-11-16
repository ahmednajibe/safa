# R2 — Claude findings (line-by-line, since R1 commit 9e85617)

Graph: graphify code-only map, 4054 nodes / 10965 edges / 182 communities, no import cycles.
God nodes: probe(), JobStore, run_pipeline(), OpContext, Timeline.

## Findings

1. **[medium] A tampered model pack can get code execution.** `backend/core/model_pack.py:174` `import_pack`.
   - The pack is verified only against its *own* SHA256SUMS.txt, so a tampered pack passes.
   - Model files include pickles: df checkpoints are loaded with `torch.load(weights_only=False)`. A malicious "offline model pack" handed to a friend therefore means code execution.
   - Fix: cross-check every file whose hash `models_manifest` knows (primary files) and refuse a mismatch. Also show the zip's SHA-256, and compare it with the release SHA256SUMS when one sits next to the zip.

2. **[medium] The output folder is allowed inside the data or models dir.** `backend/core/output_dir.py:58` `validate_output_dir` rejects only the repo ROOT.
   - In installed mode, DATA_DIR (`%LOCALAPPDATA%\Safa`) is outside ROOT. A user can set "Always save to" to a folder inside `<data>/jobs/...` or `<models>`, and retention or job delete then removes the "saved" copies.
   - Fix: also reject DATA_DIR and MODELS_DIR, plus anything inside either.

3. **[high] Linux CUDA has no working install path.**
   - The .deb/.rpm/tarball CUDA payload (~4.9 GB) is over the 2 GiB asset limit. The release now drops it (publish guard), but `install.sh --variant cuda` and README still point at `Safa-<ver>-linux-x64-cuda.tar.gz`.
   - Fix: `install.sh --variant cuda` should download the CPU tarball (or the wheel) and provision the CUDA runtime online via uv with the hashed cu124 lock, the same path as `safa setup --device cuda`. Update README/llms.txt. Alternatively, split + assemble.

4. **[medium] `test_no_system_temp_leak` is flaky by design.** `tests/test_temp_redirect.py:48`
   - It snapshots the entire system %TEMP% by name, so any other process on the machine (PowerShell writes `__PSScriptPolicyTest_*`) fails it. This happened in the M10c verify run.
   - Fix: filter to files created by the test's own process tree (e.g. names containing the worker pid, or a known prefix set: `tmp*`, `ffmpeg*`, `torch*`, `*.wav`), or set a sentinel TEMP for the child and assert that TEMP stays untouched while the system TEMP is not watched.

5. **[low] Raced name selection in `_auto_copy`.** `backend/core/output_dir.py:168`
   - `_non_colliding` then `os.replace` can overwrite a file created between the two calls, e.g. two jobs finishing together with the same output name.
   - Fix: hold a module lock around name selection + replace, or create the target with `O_EXCL` before replacing.

6. **[low] Stale local installers.** The `dist_pkg` installers predate the final uninstall-guard fix in common.iss. CI rebuilds them, so this is a note, not a code fix.

7. **[perf, carried from M4d/M10] Declick is the bottleneck.** On a 60-minute file the run takes ~66 min on GPU and ~67.5 min on CPU, dominated by the mouth-click detector and repair. It needs a profile-driven speedup that keeps the output byte-identical or within the benchmark tolerances.

## Astra partial leads (quota ran out mid-review), verified by Claude

8. **[high, CONFIRMED] Subtitles break on every fresh install.**
   - `backend/core/model_download.py:225` downloads Whisper with `snapshot_download(revision=<commit sha>)`, which writes `snapshots/<sha>/` but no `refs/main`.
   - `transcribe_child.py:78` `WhisperModel("small", download_root=...)` resolves through `faster_whisper.utils.download_model(size)`, which has no `revision` parameter in 1.1.1 and resolves `main`. Offline (HF_HUB_OFFLINE) that fails.
   - The dev machine only works because it has old `refs/main` files.
   - Fix: pass the pinned snapshot *directory* as the model path to WhisperModel when it exists, and/or write `refs/main` = revision after the pinned download. Add a test with a fake hub cache that has no refs/main.

9. **[high, CONFIRMED] The uninstaller can wipe a pre-existing folder.**
   - `common.iss` writes `.safa-data` into whatever data folder the user chose, even an existing one (Documents). The uninstall "Yes" then DelTrees the whole folder.
   - Fix: write the marker only if the folder did not exist or was empty before install. On uninstall delete only Safa's own entries (`app.db*`, `jobs`, `media`, `logs`, `models`, `tmp`, `settings`, the marker), then RemoveDir if empty. Never DelTree the root.

10. **[high, CONFIRMED] Unix installs ignore the chosen data dir.**
    - `install.sh` extracts the portable tarball including `portable.txt`, and `paths.py` gives portable.txt priority over installed.json. So `--data-dir` is ignored and data lands in the install dir.
    - deb/rpm/AppImage strip it already; install.sh and the macOS .app/.dmg must too. Check package-dmg.sh.

11. **[medium, CONFIRMED] The online installer ignores the wizard's CPU/GPU choice.**
    - `online.iss:137` `SelectedVariant()` only reads `/VARIANT` and auto-detect, never `VariantPage.Values`. A user who picks CPU on an NVIDIA machine still gets the 6 GB CUDA runtime.
    - Fix: in interactive mode use the VariantPage selection.

12. **[high, CONFIRMED] Every macOS/Linux package build fails.**
    - `scripts/package-unix.sh:57,82` call `uv pip download`, which uv does not have (no uv version does). That breaks the macOS/Linux tarballs and therefore deb, rpm, AppImage and dmg.
    - Fix: mirror package.ps1. `uv python install 3.11` into the stage, then `uv pip install --python <stage python> -r req --require-hashes --extra-index-url <torch index>`. Make the dry-run test assert that no `uv pip download` remains.

13. **[to verify] UI bugs:**
    - an upload that finishes silently stays stuck in "preparing";
    - a preview region starting after 0 compares the wrong timestamps in A/B;
    - selecting a History job can pair its output with another file's original.

## Mutation probe R2 (`.orchestrator/mutation_probe_r2.py`): 12/14 killed

14. **[test] SURVIVED: retention keep flag ignored** (`retention.py:128`). No test checks that a kept, expired job survives the sweep. Add one: an old terminal job with keep=1 must keep its files and its DB row.

15. **[test] SURVIVED: mouthclick never-louder guard removed** (`mouthclick.py:561`). No test makes a repair that would get louder and checks that it reverts. Add one: monkeypatch the repair to amplify a span (or feed a crafted input) and assert high-band energy per span ≤ original and that the guard path ran.

## Checked and OK
- Host/Origin guard: exact loopback + port.
- `SOUND_SYSTEM_BIND_HOST` defaults to loopback, with a non-loopback warning.
- Docker publishes 127.0.0.1 only.
- install.ps1 / install.sh verify SHA-256 and are iex-safe.
- The uninstaller marker guard and its overlap guard.
- Model downloads are pinned by revision + sha256.
- `_safe_rel` blocks zip traversal.
- The download endpoint uses `Content-Disposition: attachment`.
- No `dangerouslySetInnerHTML` or eval in the frontend.
- The launcher passes the port via the child env only.
