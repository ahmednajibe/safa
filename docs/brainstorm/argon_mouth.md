### 1. Mouth Clicks / Saliva Noises (Why `adeclick` Failed & Practical Fix)
* **Root cause:** `ffmpeg adeclick` runs a full-band autoregressive (AR) predictor over 55 ms windows (`w=55`). Low/mid voice energy (`80–1000 Hz`) dominates the broadband prediction error and masks high-frequency saliva ticks (`2.5–12 kHz`); lowering `adeclick`'s threshold (`t<1.5`) smears plosives (`t/k/p`). Furthermore, `enhance` currently boosts `3.5 kHz` (`+3 dB`) and `10 kHz` (`+1.5 dB`) right where wet clicks live.
* **Algorithm (`numpy`/`scipy.signal`, zero new installs — band-limited detection + AR repair):**
  * **Band-split:** Isolate the click band with a zero-phase 4th-order Butterworth bandpass (`2.5–12 kHz` via `scipy.signal.butter` + `sosfiltfilt`) and keep the `<2.5 kHz` body untouched.
  * **Detection features (1 ms hops on `2.5–12 kHz`):**
    1. *Crest / spike:* 1.5 ms RMS (or Teager-Kaiser energy $\psi[n]=x^2[n]-x[n-1]x[n+1]$) exceeding the local 40 ms sliding median + MAD floor by **$\ge 12\text{ dB}$** ($z \ge 6\,\sigma_{\text{MAD}}$).
    2. *Duration:* contiguous spike width **$0.4\text{–}6\text{ ms}$** (allowing bursts of 2–4 ticks within 25 ms if each individual pulse is $\le 6\text{ ms}$).
    3. *Band ratio:* high-band (`2.5–12 kHz`) to mid-band (`500–2500 Hz`) derivative ratio $> 4\times$.
  * **Protecting `t/k/p` consonants:** Unvoiced stops have burst + aspiration durations of **$15\text{–}60\text{ ms}$** and strong `500–2500 Hz` energy. Veto any candidate whose `2.5–12 kHz` envelope stays $>6\text{ dB}$ above the local median for **$>8\text{ ms}$** or where `500–2500 Hz` RMS rises by $>6\text{ dB}$ over a 15 ms window.
  * **Repair:** Only inside `2.5–12 kHz`, replace the flagged $0.5\text{–}6\text{ ms}$ span (+ 0.5 ms raised-cosine crossfades) using forward/backward linear prediction (Burg AR order $p=24$ fit on 10 ms clean context per side) or PCHIP envelope-matched interpolation, then recombine with the untouched `<2.5 kHz` band.

### 2. Bass / Boom: Proximity vs. Raised Floor, EQ & Expander
* **Root cause:** Both effects compound. Raising `-36 LUFS` to `-16 LUFS` adds **$+22.3\text{ dB}$** (`-81.8 -> -59.5 dBFS` floor), making 80–200 Hz room/mic resonance audible while `enhance` compresses speech peaks by ~4.3 dB. Simultaneously, phone/laptop proximity + desk bounce pack heavy energy into **`90–220 Hz`** (measured up to `-21.4 dB` in `80–160 Hz` on `tests/fixtures/real`), which a 75 Hz HPF and `-2 dB @ 250 Hz` cut miss.
* **EQ moves (in `enhance`):**
  * **HPF:** Raise to **`95 Hz`, 24 dB/oct** (two cascaded 2-pole Butterworths).
  * **Proximity / boom cut:** `PeakFilter(cutoff_frequency_hz=135, gain_db=-3.5, q=0.7)` (or `LowShelfFilter(160 Hz, -3.0 dB, Q=0.7)`).
  * **Boxiness cut:** `PeakFilter(cutoff_frequency_hz=260, gain_db=-2.5, q=1.2)`.
  * **Tame click amplification:** Trim presence boost from `+3.0 dB` to **`+1.8 dB @ 3.5 kHz` (`Q=1.0`)** and high shelf to **`+1.0 dB @ 10 kHz`**.
* **Downward expander before compressor:**
  * **Yes:** Insert `pedalboard.NoiseGate(threshold_db=thr, ratio=2.5, attack_ms=2.0, release_ms=120.0)` before the compressor.
  * **Relative threshold:** Compute dynamically per file as **`quiet_floor_dbfs + 10 dB`** (20th-percentile 20 ms frame RMS + 10 dB, clamped $\le \text{speech\_p85\_dbfs} - 18\text{ dB}$; about **`-70 dBFS`** on this recording). Also set compressor threshold relative to speech (`speech_rms_dbfs - 2 dB`, `ratio=2.5`, `attack_ms=15`, `release_ms=100`) so quiet recordings aren't mis-thresholded.

### 3. DeepFilterNet3 in Podcast Preset & Chain Order
* **Default in Podcast preset:** **ON by default, with gentler attenuation (`strength_db=12–15 dB` instead of `24 dB`)**. Even a `-82 dBFS` raw floor hits `-60 dBFS` after `+22 dB` loudnorm; a 12–15 dB cap suppresses the raised floor without metallic speech artifacts.
* **Chain order:** `highpass (95 Hz)` $\to$ `dehum` $\to$ **`denoise` (DeepFilterNet3)** $\to$ **`declick` (band-split)** $\to$ **`enhance` (Downward Expander $\to$ Subtractive EQ 135/260 Hz $\to$ Compressor $\to$ Presence EQ $\to$ De-esser)** $\to$ `silence` $\to$ `loudness`.
  * *Why:* DeepFilterNet needs stationary noise statistics before non-linear dynamics; the expander then pushes down any residual floor *before* the compressor and `+20 dB` `loudnorm`.

### 4. Objective Verification Tests on `tests/fixtures/real/*.m4a`
* **Saliva clicks & consonant safety:**
  * *Metric:* Count `2.5–12 kHz` isolated spikes ($\le 6\text{ ms}$, $>12\text{ dB}$ over 40 ms local median, no `500–2500 Hz` co-spike) after normalizing to `-16 LUFS`. **Pass:** click count reduced **$\ge 60\%$** vs raw/current M4a (`439 -> <= 175`), median `2–8 kHz` RMS on plosive windows (`15–50 ms`) drops **$\le 1.5\text{ dB}$**, and `faster-whisper` transcript matches baseline with **0 word drops**.
* **Bass / boom & quiet floor (at `-16 LUFS` output):**
  * *Metric:* 20 ms frame RMS percentiles + Welch PSD on quiet (bottom 30%) and speech (top 30%) frames. **Pass:** quiet floor **$\le -66\text{ dBFS}$**, speech-to-floor **$\ge 52\text{ dB}$** ($\ge$ raw `50.7 dB`), quiet-part `80–200 Hz` share **$\le -11.0\text{ dB}$** (vs `-6.2 dB` now), and active-speech `80–200 Hz` / `300–3000 Hz` energy ratio reduced by **$3.0\text{–}5.0\text{ dB}$**.
