Design round for F:\sound_system: what is the RIGHT ORDER of the cleanup steps? The user suspects the order changes the output (they still hear mouth clicks in their Recording after the full Podcast chain).

Read docs/SPEC.md (sections 2, 8, 10), backend/operations/registry.py (the `order` field), and the op implementations in backend/operations/impl/*.py plus backend/media/{mouthclick,dfn,room,breaths,silence_plan}.py to see what each step really does (levels, thresholds computed per file, compressor/expander in enhance, loudness gain, VAD use).

Current fixed order (backend sorts by `order`): 10 trim, 15 channel, 20 highpass, 25 dehum, 30 separate (music removal), 38 declick (mouth clicks), 40 denoise (DeepFilterNet3), 42 dereverb (auto-resonance EQ + WPE), 46 breaths (attenuate), 50 enhance (expander → HPF/boom cuts → compressor → presence EQ → de-esser), 55 enhance_ai (planned), 60 silence (cut/shorten via Silero VAD), 70 speed, 80 loudness (2-pass loudnorm + corrective gain), 90 subtitles.

Load and use the skills that fit (brainstorming, systematic-debugging). You may read files; do NOT write or change files, no installs, never delete anything. Reply max ~600 words, bullets:
1. Your recommended order, with a one-line reason for every step's position, especially: declick vs denoise vs compressor/loudness (clicks raised by gain?), dereverb vs denoise, breaths vs silence vs expander, silence before or after enhance/loudness (compressor/loudness reacting to removed pauses; VAD accuracy on cleaned vs raw audio), separate first or after highpass/dehum, trim/speed placement, subtitles last.
2. Steps that should run TWICE or be split (e.g. a light de-click again after the compressor; detection on one signal, repair on another — e.g. detect silence/breaths/clicks on the denoised signal but apply on the original?).
3. Which orders are worth testing empirically, and the metrics that would prove the best one (the orchestrator will run them on the user's 3 recordings).
4. Anything in the current implementation that makes order fragile (thresholds measured per file at the wrong point, loudness measured before cuts, etc.).
