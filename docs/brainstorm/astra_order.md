I’m using brainstorming and systematic debugging to trace how ordering affects detection, repair, and gain, then recommend a default and focused comparisons for the three recordings. This will stay read-only.Two details matter for this recommendation: `enhance` actually runs HPF/boom cuts → expander → presence EQ → compressor → de-esser, and its thresholds come from the file before those filters. The click remover also only repairs the high-frequency band, so some audible mouth sounds may survive regardless of ordering. I’m checking the existing experiments and tests to distinguish those limits from order effects.**Order matters, but the surviving clicks could also reflect detector limits.** I’d target the sequence below; moving silence earlier needs calibration changes, not just new `order` numbers.

- **1. Recommended order, for enabled steps**
  - **Trim:** exclude unwanted material before analysis and expensive processing.
  - **Channel:** fix unusable channels early; preserve useful stereo for separation.
  - **Separate:** when music exists, start with the largely untouched mixture; prefilter first only for substantial hum/rumble.
  - **Highpass:** remove rumble before level estimation and dynamics.
  - **Dehum:** remove actual mains harmonics before speech cleanup; avoid unnecessary notches.
  - **Declick:** repair sharp transients before neural processing can reshape them and dynamics can emphasize survivors.
  - **Denoise:** lower background noise before room analysis, breath detection, and VAD.
  - **Dereverb:** keep **DFN → WPE → resonance EQ** as the default; existing Recording (3) experiments support this direction. [room_findings.md](file:///F:/sound_system/docs/brainstorm/room_findings.md)
  - **Breath/silence analysis:** detect on cleaned, uncompressed audio, before breath attenuation or expansion obscures quiet events.
  - **Breath attenuation:** reduce retained breaths before compression; silence cutting alone misses breaths inside speech.
  - **Corrective EQ → expander:** suppress remaining background while natural pauses still exist; calibrate thresholds on the signal each processor actually receives.
  - **Silence cuts:** apply the saved speech/breath map before compression, so compressor release follows the final joins.
  - **Compressor → presence EQ → de-esser:** finish voice dynamics/tone on the edited programme; this proposed internal ordering needs comparison with today’s implementation.
  - **Optional light declick:** repair confirmed residuals before final normalization.
  - **Speed:** after detection/repair, preserving their millisecond assumptions; before final loudness measurement.
  - **Loudness:** measure and normalize the final edited, retimed audio. R128 gates much silence, but cuts can still change measured loudness.
  - **Subtitles:** last, giving timestamps directly in final-output time.
  - **`enhance_ai`:** leave disabled; its eventual position depends on its behavior, likely before final DSP.

- **2. Split or repeat**
  - Split `enhance` into corrective EQ/expansion and finishing dynamics. **With today’s unsplit implementation, retain enhance → silence until comparisons validate changing it.**
  - Cache speech/breath decisions before attenuation; use them for both breath treatment and silence’s breath-aware padding.
  - Detect on a cleaned analysis copy and apply aligned masks to the corresponding processing signal. Applying them to raw audio is experimental: noise, phase, latency, and repair context differ.
  - A second **gentle, conditional** declick is worth testing; repeated DFN, WPE, expansion, or normalization should not be defaults.
  - Compressor has no makeup gain, but its 15 ms attack can spare short clicks while reducing speech. Presence EQ and subsequent loudness gain can expose survivors; uniform gain alone preserves click-to-speech ratio.

- **3. Empirical comparisons**
  - On all three recordings, compare current order against: denoise→declick; declick only after enhance; early plus gentle late declick; WPE→DFN; silence before enhance; and the split design. Test separation before/after prefilters where music exists.
  - Hold parameters constant and compare at matched listening loudness. Annotate actual clicks and consonants; measure residual audibility/energy, missed clicks, false repairs, speech-boundary loss, breath attenuation, splice artifacts, LUFS and true peak.
  - Use fixed, timeline-mapped evaluation regions. Detector counts, freshly recomputed VAD duration, SRMR proxies, and clicks/minute cannot independently prove improvement after cuts.

- **4. Implementation fragility**
  - Actual enhance order is **HPF/boom → expander → presence → compressor → de-esser**, differing from the supplied description. [enhance.py:54-108](file:///F:/sound_system/backend/operations/impl/enhance.py)
  - Its all-frame P20/P85 thresholds are measured before internal EQ: cuts change those percentiles, and P20 can become quiet speech. Preserve a pre-cut noise estimate and calibrate speech separately.
  - Declick uses local thresholds, absolute −60 dBFS vetoes, restrictive duration/consonant gates, and high-band-only repair; longer or lower-frequency mouth sounds can survive any order. [mouthclick.py](file:///F:/sound_system/backend/media/mouthclick.py)
  - Loudness already follows cuts. Its corrective limiter lacks a final true-peak remeasurement.
  - Read-only inspection only; no files changed, installs, audio runs, or tests.
