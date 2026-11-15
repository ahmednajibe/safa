### 1. Recommended Order (with one-line reasons)

* **10 `trim`** — Cut unwanted head/tail first so mic-handling noise or dead air never skews per-file adaptive statistics ([trim.py](file:///F:/sound_system/backend/operations/impl/trim.py)).
* **15 `channel`** — Downmix or isolate L/R early so stereo stages (`denoise`, `dereverb`, `declick`) don't process a dead/noisy channel or do 2× work ([channel.py](file:///F:/sound_system/backend/operations/impl/channel.py)).
* **20 `highpass`** — Strip DC offset and sub-90 Hz thumps before nonlinear neural nets or notch filters see them ([highpass.py](file:///F:/sound_system/backend/operations/impl/highpass.py)).
* **25 `dehum`** — Notch stationary 50/60 Hz mains harmonics before separation/denoising so tonal hum isn't folded into vocal masks ([dehum.py](file:///F:/sound_system/backend/operations/impl/dehum.py)).
* **30 `separate`** — Isolate vocals after rumble/hum cleanup so Demucs/MDX allocates full dynamic range to voice vs. music ([separate.py](file:///F:/sound_system/backend/operations/impl/separate.py)).
* **38 `declick` (Pass 1: pre-denoise)** — Catch raw digital crackle and large impulses *before* DeepFilterNet3 (20 ms STFT) and WPE (21 ms STFT × 10–24 taps) smear them wider than the 6 ms FWHM detector gate ([mouthclick.py](file:///F:/sound_system/backend/media/mouthclick.py#L174-L179)).
* **40 `denoise`** — Remove stationary broadband noise via DeepFilterNet3 before dereverb/VAD so noise doesn't corrupt covariance or speech gates ([dfn.py](file:///F:/sound_system/backend/media/dfn.py)).
* **42 `dereverb`** — Run WPE + Welch auto-resonance EQ *after* `denoise` so stationary noise doesn't bias WPE's variance estimate or the top-40%-energy resonance finder ([room.py](file:///F:/sound_system/backend/media/room.py#L73-L173)).
* **46 `breaths`** — Detect and duck breaths on clean, dry audio *before* `enhance`'s expander/compressor alters breath envelopes and spectral flatness ([breaths.py](file:///F:/sound_system/backend/media/breaths.py#L342-L367)).
* **50 `enhance`** — Run HPF, de-boom, expander, presence EQ, compressor, and de-esser on continuous speech *before* `silence` cuts introduce 10 ms zero-fades that make envelope followers pump ([enhance.py](file:///F:/sound_system/backend/operations/impl/enhance.py#L54-L95)).
* **52 `declick` (Pass 2: post-enhance)** — **Why clicks survive today:** `denoise` drops the 2.5–12 kHz floor by ~24 dB (unmasking subtle saliva clicks that failed the `≥12 dB` excess gate in raw noise), and `enhance` boosts 3.5–10 kHz (+1.8 to +3 dB) while its 15 ms-attack compressor ducks vowels but passes 0.4–6 ms clicks untouched.
* **55 `enhance_ai`** — Generative restoration (when enabled) on fully cleaned continuous speech before timeline splices.
* **60 `silence`** — Run Silero VAD and cut pauses *after* denoise/dereverb/enhance (cleanest VAD, no compressor pumping across splices) and *before* `loudness` ([silence.py](file:///F:/sound_system/backend/operations/impl/silence.py)).
* **70 `speed`** — Time-stretch the already-shortened audio before final peak/LUFS normalization ([speed.py](file:///F:/sound_system/backend/operations/impl/speed.py)).
* **80 `loudness`** — Measure and normalize LUFS + true-peak *after* all cuts and speed edits so removed pauses don't skew LRA/gating and the +10–16 dB makeup gain is TP-limited ([loudness.py](file:///F:/sound_system/backend/operations/impl/loudness.py)).
* **90 `subtitles`** — Transcribe last so Whisper timestamps match the final edited output timeline 1:1 ([subtitles.py](file:///F:/sound_system/backend/operations/impl/subtitles.py)).

---

### 2. Steps That Should Run Twice or Be Split (Detect vs. Apply)

* **Run `declick` twice (38 pre-denoise + 52 post-enhance, or move primary mouth-declick to 44 post-dereverb + 52 post-enhance):** Pre-denoise catches raw spikes before STFT smearing; post-enhance (`z_thresh=5.5`) catches mouth clicks unmasked by DFN3 and amplified by `enhance`'s 3.5/10 kHz boosts and 15 ms-attack compressor.
* **Share breath spans from `breaths` (46) into `silence` (60) instead of re-detecting:** In [silence.py](file:///F:/sound_system/backend/operations/impl/silence.py#L151-L161), `_breaths_near_splices` re-runs `detect_breaths` on `stage_50_enhance.wav` *after* `breaths` (46) already ducked them by 12 dB and `enhance` (50) expanded them down—causing splices to miss already-attenuated breaths and leave half-gasps/clicks in the 100 ms `pad_s` zone.

---

### 3. Orders Worth Testing Empirically & Metrics

Test on the 3 recordings in `tests/fixtures/real/`:
1. **Baseline (current):** `declick(38) → denoise(40) → dereverb(42) → breaths(46) → enhance(50) → silence(60) → loudness(80)`
2. **Order A (Post-DFN declick):** Move `declick` to **44** (after `denoise`+`dereverb`, where 2.5–12 kHz noise floor is 20 dB lower).
3. **Order B (Dual declick):** `declick` at **38** AND **52** (after `enhance`).
4. **Order C (Silence before Enhance with VAD stats):** `silence(48) → enhance(50) → declick(52)`.

**Metrics to prove the winner:**
* **Residual click count & crest factor in 2.5–12 kHz:** Run `detect_mouth_clicks(..., z_thresh=5.0)` and measure 99.9th/95th percentile 1 ms high-band envelope ratio on the final −16 LUFS output.
* **Consonant preservation:** Mid/high-band spectral distortion inside Whisper-aligned sibilant/plosive spans (`/s/`, `/t/`, `/k/`).
* **Splice-edge artifact energy:** Peak 2.5–12 kHz derivative within ±20 ms of `silence` cut points.

---

### 4. Implementation Fragilities That Make Order Sensitive

* **Un-gated `level_stats` in `enhance` ([loudness.py](file:///F:/sound_system/backend/media/loudness.py#L39-L64)):** Uses raw P20/P85 over *all* frames (unlike [breaths.py](file:///F:/sound_system/backend/media/breaths.py#L171-L186) which uses VAD non-speech P10). On pause-heavy files (>20% silence), P85 (`speech_dbfs`) drops, lowering `comp_db = speech - 2` and over-compressing speech; if `silence` ran before `enhance`, P20 (`floor_dbfs`) would land inside speech. Fix: compute `floor_dbfs` on VAD non-speech and `speech_dbfs` on VAD speech.
* **`silence` re-detects breaths on post-`breaths`/post-`enhance` audio ([silence.py](file:///F:/sound_system/backend/operations/impl/silence.py#L97)):** Ducked breaths fail the `floor_db + 6 dB` seed gate ([breaths.py](file:///F:/sound_system/backend/media/breaths.py#L344)), defeating breath-aware pad clamping.
* **Presence EQ precedes Compressor in `enhance` ([enhance.py](file:///F:/sound_system/backend/operations/impl/enhance.py#L88-L94)):** Contrary to the docstring, +1.8/+3.0 dB @ 3.5 kHz and +1/+2 dB @ 10 kHz run *before* the 15 ms-attack compressor, boosting clicks while fast transients bypass compression.
* **Butterworth ringing at `mouthclick` patch edges ([mouthclick.py](file:///F:/sound_system/backend/media/mouthclick.py#L326-L354)):** `_XFADE_MS = 0.5` ms (24 samples) is shorter than the impulse response of the 2.5 kHz `sosfiltfilt` split; patching `out_high` with a 0.5 ms ramp leaves uncancelled 2.5 kHz ringing against `low`.
