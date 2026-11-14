# M4c — Voice Quality Fixes (user listening feedback) Implementation Plan

> **For agentic workers:** Implement task-by-task, test-first. Do NOT commit. Read `AGENTS.md`, `docs/SPEC.md`, `backend/operations/impl/{declick,enhance}.py`, `backend/operations/registry.py`, and the round inputs `docs/brainstorm/astra_mouth.md`, `docs/brainstorm/argon_mouth.md`. M1–M4b are done.

**Why:** The user listened to `Recording.m4a` after the M4a chain and reported (a) audible saliva / mouth clicks, (b) "something like a bass" under the voice after the volume was raised. Orchestrator measurements (original → processed): quiet-part floor −81.8 → −59.5 dBFS; speech-to-floor 50.7 → 46.4 dB; 80–200 Hz share of quiet parts −8.6 → −6.2 dB; generic transient count 439 → 436 (`adeclick` removed nothing). Analysis script: `.orchestrator/analysis/compare.py`.

**Goal:** A real mouth-click remover, a de-boomed voice polish with a pre-compression expander, and corrected chain order/presets — proven with objective metrics on the user's three real recordings.

**Tech Stack:** numpy, scipy.signal (already installed via deps — verify with `uv pip show scipy`, add with `uv add scipy` if missing), pedalboard 0.9.16, ffmpeg.

## Global Constraints
- No new heavy dependencies; offline.
- Streaming / bounded RAM: process in blocks with enough context (≥ 50 ms overlap) for the click detector.
- Length preserved exactly; output 48 kHz float32.
- Never damage consonants: the click repair only modifies the 2.5–12 kHz band, only on spans ≤ 6 ms.

## Review Focus
1. Consonant safety: on the real recordings, 2–8 kHz RMS inside detected plosive/fricative windows (15–60 ms high-band bursts) changes ≤ 1.5 dB after declick (test).
2. Transcript safety: faster-whisper small (CPU int8) transcript of each real recording after the full new chain has the same words as before processing (normalized, case/punctuation-insensitive; allow ≤ 1 word difference total and report it) (test, slow marker).
3. Expander on a recording with a NOISY floor (inject pink noise at −45 dBFS into a real recording) doesn't chop word endings: speech-band energy in the last 80 ms of each VAD speech segment drops ≤ 2 dB (test).
4. Expander/compressor thresholds are computed per file (not fixed): a recording scaled by −20 dB gets the same relative processing (output after loudness within 0.5 dB on all metrics) (test).
5. A file with no clicks (synthetic speech from fixtures) → declick changes the signal by < −60 dB residual (near no-op) (test).

---

### Task 1: Mouth-click detector + repair (`declick` mode "mouth", new default)
**Files:** Create `backend/media/mouthclick.py`; modify `backend/operations/impl/declick.py`, `backend/operations/registry.py`, `docs/UI_DESIGN.md` §7 row; tests `tests/test_mouthclick.py`.

**Interfaces — Produces:**
- `detect_mouth_clicks(x: np.ndarray, sr: int) -> list[tuple[int, int]]` (sample spans) — algorithm from the round (Argon §1 + Astra §1):
  - high band = zero-phase 4th-order Butterworth band-pass 2.5–12 kHz (`sosfiltfilt`); mid band 500–2500 Hz.
  - Teager-Kaiser energy (or 1.5 ms RMS) of the high band on 1 ms hops; local floor = 40 ms sliding median + MAD; candidate when ≥ 12 dB above floor (z ≥ 6 MAD).
  - keep candidates with contiguous width 0.4–6 ms and high/mid derivative ratio > 4×.
  - **veto** (consonant protection): high-band envelope stays > 6 dB over local median for > 8 ms, or mid-band RMS rises > 6 dB within ±15 ms.
- `repair_mouth_clicks(x: np.ndarray, sr: int, spans) -> np.ndarray` — split into low (< 2.5 kHz, complementary filter so low+high reconstructs x within −80 dB) and high; in the high band only, replace each span (+0.5 ms raised-cosine crossfades) with bidirectional Burg/least-squares AR prediction (order 24, 10 ms context each side), fall back to PCHIP for spans < 1 ms; recombine.
- `declick` params: `mode` select `mouth` ("Mouth clicks and saliva", default) | `crackle` ("Digital crackle", the existing adeclick mapping); `sensitivity` gentle | normal | strong → z threshold 7 / 6 / 5. Streaming in 10 s blocks with 50 ms overlap.
- UI_DESIGN §7: `declick` → "Remove mouth clicks" — "Removes saliva clicks, lip smacks and crackle without touching consonants."
- Tests: synthetic — 30 injected 1–4 ms band-limited clicks into SAPI speech → ≥ 80 % detected, ≤ 2 false positives per 10 s; Review Focus 1, 5; low+high reconstruction exactness; real recordings: click count (by the detector at normal sensitivity, run on the output) reduced ≥ 60 % vs input — and report the number.

### Task 2: Voice polish rework (`enhance`)
**Files:** Modify `backend/operations/impl/enhance.py`, `backend/media/loudness.py` (add `level_stats(wav) -> {"floor_dbfs","speech_dbfs"}` using 20 ms frame RMS percentiles: floor = 20th pct, speech = 85th pct); tests `tests/test_op_enhance.py`.

New chain for preset **voice** (bright/warm derive from it as before):
1. **Downward expander** before anything that adds gain: `pedalboard.NoiseGate(threshold_db=T, ratio=2.5, attack_ms=2, release_ms=150)` with `T = clamp(floor_dbfs + 10, max = speech_dbfs − 18)` measured on the op's input (Review Focus 4).
2. HighpassFilter 95 Hz ×2 (24 dB/oct).
3. PeakFilter 135 Hz −3.5 dB Q 0.7 (boom), PeakFilter 260 Hz −2.5 dB Q 1.2 (boxiness).
4. Compressor threshold = `speech_dbfs − 2`, ratio 2.5, attack 15 ms, release 100 ms; make-up gain 0 (loudness op handles level).
5. PeakFilter 3.5 kHz **+1.8 dB** Q 1.0, HighShelfFilter 10 kHz **+1.0 dB**.
6. ffmpeg `deesser` (if `deess`).
- New param `reduce_boom` (bool, default true, label "Reduce boominess") toggles step 3.
- Tests: Review Focus 3, 4; on each real recording after enhance + loudness(−16): quiet floor ≤ −66 dBFS, speech-to-floor ≥ 50 dB, quiet-part 80–200 Hz share ≤ −11 dB, voiced 80–200 / 300–3000 Hz energy ratio reduced by 3–5 dB vs the M4a chain, loudness −16 ±0.5 LUFS, TP ≤ −1 dBTP (use/port the metrics from `.orchestrator/analysis/compare.py` into `tests/audio_metrics.py`).

### Task 3: Order + presets + defaults
**Files:** Modify `backend/operations/registry.py`, `docs/SPEC.md` §2 table, `tests/test_operations.py`.
- Move `declick` before `denoise`: `declick.order = 38` (after dereverb 35, before denoise 40) — orders must stay unique.
- `highpass.cutoff_hz` default 90.
- Presets: **podcast** = highpass, dehum, declick(mouth), denoise(ai, strength_db 12), enhance(voice), silence(remove), loudness(podcast). **lecture** same + subtitles. **jump_cut** = declick, denoise(12), enhance, silence(remove), loudness(youtube). (denoise is implemented in M6; until then resolve marks it unavailable/not-implemented and the rest runs — make sure that path doesn't fail the job: unimplemented ops in a preset are dropped into `disabled` with "Coming in a later update" rather than raising.)
- SPEC §2 table updated (declick name/engine/order, enhance description, highpass default).

### Task 4: Listening files + report
- Produce for each of the three real recordings, via the jobs pipeline with the Podcast preset (minus not-yet-implemented ops): `data/review/m4c_<stem>.wav`, plus `data/review/m4c_<stem>_clicks_removed.wav` = input − declick output (the removed signal, so the user can hear that only clicks were taken out).
- Report a table per recording: before (M4a chain) vs after — floor, speech-to-floor, quiet 80–200 share, voiced boom ratio, click count, LUFS, TP, transcript diff.
- [ ] `uv run pytest -v` → all pass.

## Final report
Files, commands + outcomes, the metrics table, deviations, open problems.
