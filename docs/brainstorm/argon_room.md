### 1. Hypothesis & Blind Mono Metric
- **Hypothesis is right:** [try_dereverb.py](file:///F:/sound_system/.orchestrator/analysis/try_dereverb.py) uses music-stem models (`UVR-DeEcho-DeReverb`, `anvuew` RoFormer) trained on long diffuse stereo tails ($T_{60} > 0.8\text{ s}$). A laptop mic on a desk in a small room suffers from **early reflections ($1\text{–}50\text{ ms}$)** and **modal resonances ($250\text{–}900\text{ Hz}$)** that music models treat as dry vocal timbre, while continuous speech (87% duty cycle) + $-27.8\text{ dBFS}$ fan noise ([try_dfn.py](file:///F:/sound_system/.orchestrator/analysis/try_dfn.py)) mask the short decay tails.
- **Practical `numpy`/`scipy` blind metric (2-part, no reference):**
  1. **Syllabic-to-Reverb Modulation Ratio (SRMR proxy):** Bandpass voiced speech ($300\text{–}4000\text{ Hz}$), compute 10 ms log-RMS envelope, and take its Welch PSD. Compute the ratio of syllabic modulation energy ($2\text{–}8\text{ Hz}$) to smeared modulation ($16\text{–}40\text{ Hz}$) plus overall envelope dynamic standard deviation $\sigma_{\text{dB}}$. Early reflections fill inter-syllable dips; dereverb raises $E_{2\text{–}8\text{Hz}}/E_{16\text{–}40\text{Hz}}$ and $\sigma_{\text{dB}}$.
  2. **LTAS Spectral Ripple Variance ($220\text{–}1000\text{ Hz}$):** Compute voiced Welch log-PSD (`nperseg=4096`), subtract a smoothed 1/3-octave median curve (`scipy.ndimage.median_filter`), and measure RMS dB deviation + peak prominence (`scipy.signal.find_peaks`). Directly quantifies comb-filtering/boxy room modes.

### 2. Offline Speech Dereverberation Ranked (Win11 / Py3.11 / GTX 1650 4 GB)
1. **`nara_wpe` / Native Torch WPE (Weighted Prediction Error)**
   - *Install risk:* **None/Low** (pure NumPy/SciPy package, or ~45 lines of `torch.linalg.solve` STFT linear prediction with zero new deps).
   - *Small-room quality:* **Moderate** — strips reflections $>15\text{ ms}$ cleanly with zero vocoder artifacts; needs EQ pairing for $<10\text{ ms}$ coloration.
   - *Speed/VRAM:* ~0.4 s GPU (<150 MB VRAM) or ~2 s CPU per 30 s. *License:* **MIT**.
2. **`DeepFilterNet3` with Post-Filter (`post_filter=True` in `init_df`)**
   - *Install risk:* **Zero** (already in [pyproject.toml](file:///F:/sound_system/pyproject.toml)).
   - *Small-room quality:* **Low-Moderate** — attenuates low-SNR reverberant bins by an extra $3\text{–}5\text{ dB}$, but preserves early boxy modes.
   - *Speed/VRAM:* 0.5 s GPU (106 MB) / 1.6 s CPU ([FEASIBILITY.md](file:///F:/sound_system/docs/FEASIBILITY.md)). *License:* **MIT/Apache-2.0**.
3. **`ClearerVoice-Studio` (`MossFormer2_SE_48K` / `FRCRN`)**
   - *Install risk:* **Medium** (install `--no-deps` to protect `numpy==1.26.4` and `torch==2.5.1+cu124`).
   - *Small-room quality:* **Good** — 48 kHz joint speech denoise+dereverb without heavy vocoder sheen.
   - *Speed/VRAM:* ~3–5 s GPU (~1.2 GB VRAM), ~20 s CPU. *License:* **Apache-2.0**.
4. **`resemble-enhance` (UNet Denoiser + CFM Enhancer)**
   - *Install risk:* **High** unless `--no-deps` + inference shim (upstream pulls `deepspeed` and pins old `torch`).
   - *Small-room quality:* **High** on boxy coloration (resynthesizes dry close-mic speech), but generative CFM can slightly shift speaker timbre/accents.
   - *Speed/VRAM:* **Slow** on GTX 1650 (no Tensor Cores): ~15–25 s GPU (~2 GB VRAM), >90 s CPU. *License:* **MIT**.
5. **`VoiceFixer` / `SGMSE+` / `Miipher`**
   - *Why lower:* `VoiceFixer` (MIT, ~4 s GPU) imparts TFGAN vocoder harshness; `SGMSE+` (MIT) is 16 kHz and takes ~30 s diffusion steps; `Miipher` lacks official weights and drifts on Arabic/accents.

### 3. Classic DSP Fallback for Boxiness/Hollowness
- Currently [enhance.py](file:///F:/sound_system/backend/operations/impl/enhance.py#L70-L73) only cuts fixed `135 Hz` and `260 Hz`, missing the **$300\text{–}850\text{ Hz}$ bathroom/boxy zone**:
  1. **Auto-Resonance Notch EQ:** On voiced frames, compute 4096-pt Welch log-PSD, subtract a 1/3-octave median baseline across $220\text{–}1000\text{ Hz}$, find top 3 resonant peaks (`prominence >= 2.5 dB`), and insert narrow `pedalboard.PeakFilter` cuts ($-2.5\text{ to }-6\text{ dB}$, $Q=3.5\text{–}5.0$) plus a broad $-2\text{ dB}$ dip at $420\text{ Hz}$ ($Q=0.9$).
  2. **Mid-Band Envelope Tail Suppressor:** In $250\text{–}2500\text{ Hz}$, compare fast ($5\text{ ms}$) vs. slow ($80\text{ ms}$) RMS envelopes; attenuate decaying inter-syllable tails by up to $-6\text{ dB} \times \text{strength}$.

### 4. Recommendation & Pipeline Order
- **Swap order so `denoise` (DFN3) runs BEFORE `dereverb`** (move `denoise` before `dereverb` in [registry.py](file:///F:/sound_system/backend/operations/registry.py#L113-L136)): fan noise at $-27.8\text{ dBFS}$ corrupts WPE covariance estimation and hides reverb tails from envelope expanders.
- **Implement `dereverb` as a fast, deterministic 3-step chain:** **STFT-WPE** ($\Delta=2, K=12$ taps for tails $>15\text{ ms}$) $\rightarrow$ **Auto-Resonance Notch EQ** ($220\text{–}1000\text{ Hz}$ for early-reflection boxiness) $\rightarrow$ **Envelope Tail Suppressor**, keeping generative `resemble-enhance` strictly for `enhance_ai` (order 55).
