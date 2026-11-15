# M4d — Processing Order + In-Speech Mouth Clicks Implementation Plan

> **For agentic workers:** Implement task-by-task, test-first. Do NOT commit. Read `AGENTS.md`, `docs/SPEC.md`, and the inputs of this milestone: `docs/brainstorm/{argon,astra}_order.md`, `docs/brainstorm/{argon,astra}_click2.md`, the orchestrator experiments `.orchestrator/analysis/{order_experiment.py,order_experiment_g.py,residual_clicks.py}` and their results (`data/review/order_experiment*/results.json`). M1–M9a + R1 are done (M9b may be in progress in parallel — do not touch `frontend/` beyond i18n keys for new message codes/op names).

**Why (user feedback):** the user still hears mouth clicks (طقطقة الفم) in `Recording.m4a`, and suspected the step order changes the output. Evidence:
- **Order experiment** (Podcast params, 3 real recordings): order **G = … declick → denoise → dereverb → breaths → silence → enhance → declick(again) → loudness** cut clicks/min on Recording 33.5 → 22.9 (−32 %) and raised the pause gap 16.4 → 37.0 dB vs the current order; but on Recording (3) VAD speech dropped 26.0 → 25.3 s — because `enhance` calibrates its expander on all-frame P20/P85 measured *after* the pauses are gone (P20 lands in quiet speech). Both Astra and Argon predicted this.
- **Detector analysis (Argon, measured):** in-speech recall of synthetic clicks is only **48 %** at z=6: vowel glottal modulation (F0 105–125 Hz) trips the mid-band vetoes (14/21 misses); MAD self-masking inside speech/bursts (8/21); unconstrained least-squares AR **amplified** a click by +11.2 dB at 30.227 s. Astra: disabling only the mid-specificity veto raises z=6 detections 61 → 99; 53/64 residuals were never treated.
- Denoise lowers the 2.5–12 kHz floor ~24 dB (unmasks small clicks); `enhance` boosts 3.5/10 kHz and its 15 ms-attack compressor lets 1–6 ms clicks through; loudness then adds up to +22 dB.

## Global Constraints
- Never make anything louder by repair: every repaired span's high-band energy change ≤ 0.0 dB (assert in code + tests).
- `< 2.5 kHz` band bit-identical through the click repair (complementary split).
- Speech must never be lost by the new order: on each real recording, output VAD speech seconds ≥ 99 % of the current-order output's.
- Streaming/bounded RAM as before; CPU only; all summaries via `msg()` codes (+ i18n keys EN/AR in `frontend/src/i18n` for any new code/op name).

## Task 1 — New order + carried analysis
- Registry orders become: trim 10, channel 15, highpass 20, dehum 25, separate 30, declick 38, denoise 40, dereverb 42, breaths 46, **silence 48**, **enhance 50**, **declick_final 52 (hidden, auto)**, enhance_ai 55, speed 70, loudness 80, subtitles 90. Keep orders unique; update SPEC §2 table.
- `declick_final`: hidden op (not listed in `/api/operations`, no UI card) auto-inserted by `resolve()` right after `enhance` whenever `declick` is enabled; runs the new detector in **residual** mode (gentler, see Task 2); stage name "Remove mouth clicks (final pass)" / "إزالة طقطقة الفم (المرور الأخير)"; reported in Results like any stage.
- `OpContext.analysis: dict` carried through the runner across stages (a plain dict persisted in the worker only). Stages write measurements, later stages read them:
  - `denoise`/first stage that runs VAD writes `noise_floor_dbfs` (VAD non-speech P10) and `speech_dbfs` (VAD speech P85) **measured before any cut**;
  - `breaths` writes the detected breath spans (in its input time) → `silence` reuses them (mapped through the timeline) instead of re-detecting on already-ducked audio.
- `enhance` calibration: use `analysis` values when present; otherwise compute **VAD-gated** stats (floor on non-speech, speech on speech) on its own input — never all-frame P20/P85.
- `enhance` internal order → HPF/boom cuts → expander → **compressor → presence EQ → de-esser** (Astra/Argon: presence boost no longer feeds the compressor; the final declick pass handles exposed clicks). Update the module docstring to match the code.
- Loudness: after the corrective limiter pass, re-measure true peak and assert ≤ −1.0 dBTP (+0.1 tolerance).
- Tests: order/registry/resolve tests; `declick_final` auto-insert/hidden; analysis carried; enhance uses VAD-gated stats (unit on synthetic pause-heavy file: expander threshold below quiet-speech level); **speech-preservation e2e on the 3 real recordings (skip if missing): new order speech_s ≥ 99 % of old order**, pause gap ≥ old, clicks/min ≤ old.

## Task 2 — In-speech mouth-click detector + repair (rewrite `backend/media/mouthclick.py`, keep its public API)
Follow `argon_click2.md` §1–§2 and `astra_click2.md` §1–§2:
- Complementary split at 2.5 kHz (low band untouched).
- High-band short STFT (n_fft 256, Hann 128 = 2.67 ms, hop 32 = 0.67 ms) + a 20–32 ms window for harmonic context. Per bin temporal median over ±15 ms (trimmed/two-stage so bursts don't raise it) and **MAD capped at 3 dB**; flag bin-frames with excess ≥ 7 dB (normal; gentle 9, strong 5; residual mode 8).
- A candidate needs ≥ 30 % of 4–14 kHz bins flagged in the same 0.5–3.5 ms event (−12 dB width ≤ 3.5 ms).
- **Consonant protection by persistence, not mid-band level:** `binary_opening` of the excess mask with ≥ 6 ms per-bin persistence removes fricative/aspiration tails (t/k/p/s/sh/ch −12 dB widths are 9–30 ms); word-initial plosive veto only when < 2.5 kHz jumps > 10 dB within 2 ms after ≥ 15 ms of < −50 dBFS AND a > 8 ms high-band hold follows. **Remove the mid-rise / mid-excess vetoes.**
- **GCI comb veto:** reject candidates whose 4.5–14 kHz energy is > 25 dB below 0.5–2.5 kHz and that repeat at the pitch period (6–12 ms, ±2.5 dB).
- Bursts: events within 50 ms are grouped, excluded from the baseline, repaired per tick.
- Repair: isolated ticks ≤ 3 ms → high-band time-domain fill over `[c0−0.5 ms, c1+1.5 ms]` with **Burg AR (order 24, |k| < 0.995), fit only on unflagged samples**; bursts / > 3 ms / in-speech → per-bin STFT **magnitude clamping to the median, original phase kept**, mask smoothed over 3 bins; crossfades ≥ 2 ms (no 2.5 kHz ringing at patch edges). Hard guard: if a repaired span's high-band energy rises, revert that span.
- Residual mode (for `declick_final`): same detector, threshold 8 dB, only magnitude clamping.

## Task 3 — Tests (gates from both reviews)
- Synthetic in-speech benchmark: 50 band-limited clicks (3.5–14 kHz, 0.5–2.5 ms, +10…+16 dB local SNR) + 10 bursts (3–4 ticks / 35 ms) injected into voiced and consonant regions of `Recording.m4a` and SAPI speech (fixed seed; evaluation passages separate from any tuning): **recall ≥ 85 %, precision ≥ 95 %, median injected-click high-band energy reduction ≥ 10 dB, max span gain ≤ 0.0 dB, un-injected consonant 2.5–12 kHz RMS change ≤ 0.5 dB (95th pct ≤ 1.0 dB), faster-whisper word diff = 0** (whisper part may be slow-marked).
- The existing mouth-click, declick, enhance, silence, breaths and e2e tests stay green (update only where the new order/behaviour is the intended change, with a reason in the test).
- Re-run the orchestrator order experiment script and report the new numbers (current-order baseline vs new pipeline) for all 3 recordings.

## Task 4 — Listening audit for the user
`scripts/make_review_files.py --m4d`: for `Recording.m4a`:
- `data/review/m4d_Recording_podcast.wav` (new pipeline) and `m4d_Recording_podcast_old_order.wav` (current pipeline) at −16 LUFS;
- `data/review/click_audit_numbered.wav`: the top ~30 click candidates of the ORIGINAL by detector score (and any that remain), each: beep + spoken-free index tones (n short beeps for tens/units are confusing — use a 1 kHz beep then 0.15 s gap), **A (before, 0.7 s context) → 0.15 s gap → B (after)**, normalized; plus `data/review/click_audit_numbered.txt` table `#idx  t_sec  class(click/burst)  repaired(yes/no)`. The user will answer "still clicks: […], damaged consonant: […]".

## Done when
All tests green (non-slow + slow once), gates met, numbers reported, review + audit files written.
