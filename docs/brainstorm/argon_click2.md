Running the read-only STFT ridge and consonant-protection analysis on `Recording.m4a` in memory; waiting for the task to finish.
### Root Cause from In-Memory Probe on `Recording.m4a`
Testing [mouthclick.py](file:///F:/sound_system/backend/media/mouthclick.py) on 40 synthetic `+14 dB` (`3.5–12 kHz`, `1.5 ms`) clicks injected into active speech (`mid > -38 dBFS`) showed **only 48% recall at `z=6`** (21/40 missed):
- **Mid-band vowel modulation vetoed 14/21 clicks:** Glottal pitch pulses (`F0 ≈ 105–125 Hz`) naturally modulate `500–2500 Hz` by `6–14 dB` every `8–9 ms`, tripping `excess_mid` ([mouthclick.py](file:///F:/sound_system/backend/media/mouthclick.py#L189-L193)) and `_MID_RISE_DB` ([mouthclick.py](file:///F:/sound_system/backend/media/mouthclick.py#L222-L229)) even when the click added zero `<2.5 kHz` energy.
- **MAD self-masking hid 8/21 clicks:** Inside speech or wet bursts (`18.67–18.71 s`, `19.04–19.25 s`, `26.24–26.40 s`), 40 ms `mad_hi` inflates to `4.5–9.2 dB`, adding `+7 to +13.5 dB` to `floor_db`.
- **Unconstrained OLS AR instability:** `_ar_predict` ([mouthclick.py](file:///F:/sound_system/backend/media/mouthclick.py#L241-L259)) uses `np.linalg.lstsq` (non-minimum-phase poles $|z|>1$) and fits on contaminated adjacent ticks; at `30.227 s` it **boosted** the high band by `+11.2 dB` (`-70.5 → -59.3 dBFS`). Nine of the 64 `z=4` residuals overlap `z=6` spans (`19` within `5 ms`).

---

### 1. Best In-Speech Declick Method & Pretrained Model Reality
* **Pretrained models (honest status):** **None exist** for isolated mouth-click removal. Open speech-restoration models (`Resemble-Enhance`, `VoiceFixer`, `CleanUNet`, `MossFormer2`, `DeepFilterNet3`) operate on `10–20 ms` STFT frames and either pass `1–3 ms` saliva ticks untouched or hallucinate/oversmooth `t/k/s`. Commercial declickers (iZotope RX, Accentize DeRoom/DeClick) are proprietary.
* **Best DSP architecture (Hybrid Band-Split + Short-Window STFT Ridge Attenuation + Burg AR):**
  1. **Complementary split at `2.5 kHz`:** Keep `<2.5 kHz` 100% bit-identical (`split_bands`).
  2. **Short-STFT broadband ridge detection (`>2.5 kHz`):** Compute STFT (`n_fft=256`, Hann `nperseg=128` = `2.67 ms`, `hop=32` = `0.67 ms`). Per frequency bin $k$, compute temporal median $M_{k,t}$ over `±15 ms` (45 frames) and capped MAD $\sigma_{k,t} = \min(\text{MAD}_{k,t}, 3.0\text{ dB})$ so speech/bursts cannot self-mask. Flag bin-frames with excess $E_{k,t} = Y_{k,t} - (M_{k,t} + 1.48\,\sigma_{k,t}) \ge 7\text{ dB}$.
  3. **Repair:**
     * **Isolated single ticks (`≤3 ms`):** Replace `[c0 - 0.5 ms, c1 + 1.5 ms]` (covering the IIR decay tail) in the `>2.5 kHz` time domain using **Burg's AR method** (`order=24`, reflection coefficients $|k_m| < 0.995$ guaranteeing stability, fit only on unflagged samples).
     * **In-speech & wet bursts (`>3 ms` or multiple ticks):** Perform **per-bin STFT magnitude clamping** on flagged frames ($G_{k,t} = 10^{(M_{k,t} - Y_{k,t})/20}$ only where $E_{k,t} > 3\text{ dB}$, smoothed across 3 frequency bins, **keeping original phase $\angle Z_{k,t}$** so unflagged harmonics reconstruct losslessly via `istft`).

### 2. Separating In-Speech Clicks from `t/k/p/s/ch` & Glottal Pulses, Plus Bursts
* **Mouth click vs. Consonant (`t/k/p/s/ch`) vs. Glottal Closure Instant (GCI):**
  * **Duration & morphological opening:** On `Recording.m4a`, all 30 probe clicks have `-6 dB` width `0.5–2.5 ms` and `-12 dB` width `1.5–3.5 ms`, whereas all 27 consonants have `-12 dB` width **`9.0–30.5 ms`**. Applying binary opening `ndimage.binary_opening(E > 4 dB, size=(1, 9))` (`≥6 ms` persistence per bin) masks out every fricative/plosive aspiration tail before ridge scoring.
  * **Upper-band prominence (`4.5–14 kHz`) & GCI comb veto:** Loud vowel GCIs (`2.34, 3.50, 3.52, 7.06, 26.24–26.28 s`) peak in `F3` (`2.5–3.8 kHz`) with `4.5–14 kHz` `28–48 dB` below `0.5–2.5 kHz` and repeat at pitch period $T_0 \in [6, 12]\text{ ms}$. Require **≥30% of bins in `4.0–14 kHz` to exceed `M + 8 dB`** AND veto candidates where `4.5–14 kHz` is `>25 dB` below `0.5–2.5 kHz` with periodic neighbors at $t \pm T_0$ (within `2.5 dB`).
  * **Drop the `mid_rise` / `mid_excess` vetoes inside active speech:** Replace them with a **coincident full-band stop-release veto**: only veto when `<2.5 kHz` jumps `>10 dB` within `2 ms` *preceded by `≥15 ms` of low energy (`<-50 dBFS`)* (true word-initial plosive release) AND followed by `>8 ms` high-band hold.
* **Handling bursts ("wet mouth", `2–6` ticks in `50 ms`):**
  * Cap per-bin `MAD` at `3.0 dB` and use a **two-stage median** (or trimmed 30th-percentile baseline over `50 ms`) so closely spaced ticks (`18.67–18.71 s`) do not raise the baseline. Repair bursts strictly via **STFT per-bin magnitude clamping** (never time-domain AR across a `15–40 ms` burst, which either bridges across ticks or diverges).

### 3. Pipeline Chain Order ([registry.py](file:///F:/sound_system/backend/operations/registry.py))
* **Keep primary `declick` at `order=38` (before `denoise`), AND run a lightweight post-`enhance` declick pass inside `declick`/`enhance` (or move `declick` to `order=52` right after `enhance`):**
  * In-memory order testing on `Recording.m4a` (`orig z4=105`): `declick → enhance` left `z6=10, z4=61` because [enhance.py](file:///F:/sound_system/backend/operations/impl/enhance.py#L58-L94) applies `+1.8 dB @ 3.5 kHz`, `+1.0 dB @ 10 kHz`, and a `15 ms`-attack compressor (which passes `<3 ms` clicks uncompressed while compressing speech by `~4 dB`), followed by `+22 dB` `loudness`. Running `declick` before `denoise` prevents clicks from exciting DeepFilterNet3, and a second high-band ridge clamp after `enhance` dropped residuals to `z6=3, z4=53`.

### 4. Objective & Human-in-the-Loop Test Plan
* **Synthetic in-speech benchmark (`pytest`):**
  * Inject 50 synthetic band-limited (`3.5–14 kHz`, `0.5–2.5 ms`, `+10 to +16 dB` local high-band SNR) single clicks and 10 multi-tick bursts (`3–4` ticks in `35 ms`) into voiced/consonant regions of `Recording.m4a` and SAPI speech.
  * **Pass gates:** In-speech click recall **$\ge 85\%$** (vs 48% today), median click high-band energy reduction **$\ge 10\text{ dB}$**, zero spans with positive high-band energy gain (`max_span_gain_db <= 0.0 dB`, catching AR blowups), uninjected consonant (`t/k/p/s/sh`) `2.5–12 kHz` RMS drop **$\le 0.5\text{ dB}$** (`95th pct ≤ 1.0 dB`), and `faster-whisper` word error diff `= 0`.
* **Fast user ground-truth protocol:**
  * Generate a single `data/review/click_audit_numbered.wav` containing the top ~30 candidate snippets (`0.6 s` context each), preceded by spoken TTS/beep index numbers (`#1..#30`) where each item plays **A (before) → 0.15 s gap → B (repaired)** normalized to `-16 LUFS`, plus a printed timestamp table (`#idx: t_sec, class`). The user replies with just two short lists of numbers: *"still clicks: [..], damaged consonant: [..]"*.
