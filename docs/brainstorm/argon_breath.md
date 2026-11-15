### 1. Attenuate vs. Delete & Envelope Design
- **Attenuate-by-default is right**: Hard-deleting (muting to zero) creates an unnatural "vacuum" below room tone; time-cutting short breaths crashes phrases together.
- **Target & floor clamp**: Default **−12 dB** (range −6 to −20 dB), clamped so attenuated breath RMS never drops below `floor_dbfs + 2 dB` (from [loudness.py](file:///F:/sound_system/backend/media/loudness.py#L39-L64)).
- **Smooth gain envelope (no gating chatter)**: Compute gain at 10 ms hop; apply **20 ms raised-cosine attack** into attenuation, hold across the breath core, and **35 ms release**—shortened to a **15 ms fast release** if a speech onset immediately follows so initial consonants aren't ducked.
- **When full removal wins**: In [silence.py](file:///F:/sound_system/backend/operations/impl/silence.py) (`action="remove"`, video jump-cuts). Note a key quirk in [silence_plan.py](file:///F:/sound_system/backend/media/silence_plan.py#L41-L67): `pad_s` (0.1 s) is added to speech blocks *before* checking `gap < min_silence_s` (0.3 s), so raw pauses `< 0.5 s` are kept whole, and cuts `≥ 0.5 s` still keep 100 ms of pre-speech breath. In `remove` mode, clamping `pad_s` so it doesn't expand into a detected breath avoids clipped half-gasps at splices.

### 2. Detector Features, Consonant Protection & Models
- **Pretrained models**: None worth bundling. UVR `MDX-B De-Breath` ONNX weights (~70 MB) target singing, are heavy on CPU/VRAM, and have murky training licenses. Pure CPU DSP (`numpy`/`scipy` + [silence_detect.py](file:///F:/sound_system/backend/media/silence_detect.py)) runs in <0.15 s/min.
- **Features & thresholds (20 ms frame, 10 ms hop)**:
  - **Silero VAD**: frame `p_speech < 0.35` (or `< 0.55` when within 150 ms before a VAD onset).
  - **Level**: frame RMS in `[floor_dbfs + 6 dB, speech_dbfs - 8 dB]`.
  - **Unvoiced (autocorrelation peak in 70–400 Hz)**: normalized `r_max < 0.35` (rejects vowels, voiced sighs, soft voiced starts).
  - **Spectrum**: band-energy ratio `E(0.4–4.5 kHz) / E_total > 0.65`, HF ratio `E(5.5–16 kHz) / E(0.4–4.5 kHz) < 0.8`, spectral flatness (0.4–6 kHz) `> 0.22`, centroid **800–3200 Hz**.
  - **Duration & shape**: contiguous span **160–900 ms** (merge micro-gaps ≤ 30 ms) with gradual onset (`time_to_peak ≥ 40 ms`).
- **Never hitting `/s, f, h, sh/` or soft word starts**:
  - **Sibilant guard**: `/s, sh, f/` have centroid `> 3.5 kHz` and `E(5.5–16 kHz)/E(0.4–4.5 kHz) > 1.0` → rejected by spectral rules.
  - **Duration guard**: Phonemic `/h, f, s/` last **30–130 ms** → rejected by the **≥ 160 ms** minimum duration.
  - **Voiced-onset collar**: Backtrack from the first voiced frame (`r_max ≥ 0.45`) of each word and **hard-protect 45 ms before voicing** so pre-aspirated `/h/` and soft word attacks are untouched.

### 3. Pipeline Placement & Stage Interactions
- **Where it belongs**: Add `reduce_breaths: bool = True` (plus advanced `breath_db: float = 12`) to **`enhance`** ([enhance.py](file:///F:/sound_system/backend/operations/impl/enhance.py), order 50), backed by a shared `backend/media/breaths.py` detector that **`silence`** (order 60) can also use to trim breath edges from `pad_s`.
- **Stage interactions**:
  - **After `denoise` (40)**: Stationary hiss is already gone, making flatness/centroid reliable.
  - **Before `enhance` EQ/Gate/Comp (50)**: Attenuating breaths *first* prevents the +1.8/+3 dB 3.5 kHz presence EQ and compressor from boosting them, and drops breath tails below the `NoiseGate` threshold (`min(floor+10, speech-18)`) so the expander closes smoothly instead of chattering.
  - **Before `loudness` (80)**: Preserves the −12 dB breath-to-speech margin when 2-pass `loudnorm` raises overall gain.

### 4. Objective Tests & Consonant Safety Proof
- **Synthesizing realistic breaths**: White noise → 2nd-order bandpass `400–4200 Hz` + two vocal-tract formant peaks (`~750 Hz` and `~1750 Hz`, Q=2, +6 dB) → 350 ms raised-cosine/Beta(2,2) envelope at −24 dBFS, inserted 40 ms before SAPI word onsets and inside 0.35 s pauses.
- **Pass criteria**:
  - *Synthetic*: Breath span RMS drops by `12 ± 2.5 dB`; speech RMS delta `≤ 0.1 dB`; no click at envelope boundaries.
  - *Real (`tests/fixtures/real/*.m4a`)*: Inter-phrase non-speech P90 RMS drops **≥ 6 dB** vs. `enhance` with `reduce_breaths=False`.
  - *Consonant safety proof*: Run on a breath-free SAPI passage packed with `/s, f, h, sh/` (*"she sells five fast horses, happy home"*); assert **0 detected breaths** and **< 0.2 dB** RMS / 4–10 kHz band change across all `[onset - 50 ms, onset + 150 ms]` windows.
