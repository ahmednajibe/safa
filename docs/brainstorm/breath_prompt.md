Design round for F:\sound_system (local audio cleanup app; installed stack in docs/FEASIBILITY.md: numpy/scipy, silero-vad 5.1.2, DeepFilterNet3, pedalboard, ffmpeg 7.1, torch 2.5.1 on GTX 1650; Python 3.11 Windows).

The user asks: what happens when I stop talking but take a deep breath?
Current behaviour (read backend/operations/impl/silence.py, backend/media/silence_detect.py, backend/media/silence_plan.py, backend/operations/impl/enhance.py, docs/SPEC.md section 10):
- Cut silence (remove mode) uses Silero VAD; breaths are usually non-speech so a breath inside a gap >= 0.3 s is removed with the silence; but 0.1 s padding around speech can keep breath edges, and loud breaths may be classified as speech.
- Without Cut silence, breaths stay and the expander/compressor/loudness chain makes them louder.
Claude's proposal: a Reduce breaths option that attenuates breaths (default about -12 dB) instead of deleting them; detection = non-speech-ish sounds 0.2-0.8 s, noise-like (no pitch, high spectral flatness), energy mostly 500 Hz-5 kHz; integrates with Cut silence.

Load and use the skills that fit (brainstorming, systematic-debugging). You may read files; do NOT write or change files, no installs, never delete anything. Reply max ~450 words, bullets:
1. Is attenuate-not-delete right? Default amount, fades/attack/release so it does not sound gated; any case where full removal is better.
2. Best detector with what we have: features and thresholds (pitch/voicing e.g. autocorrelation or YIN, spectral flatness, centroid, band energy, duration, position next to speech onsets, Silero speech prob), how to avoid hitting unvoiced consonants (s, f, h, sh) and soft word starts; is there a pretrained breath detector usable offline on Windows (license, size)?
3. Where it belongs: separate op (order?) vs a param of silence vs of enhance; interaction with the expander, silence padding, and loudness.
4. Objective tests: synthetic breaths (how to synthesize realistic ones) plus metrics/thresholds on real speech; how to prove we never damage s/f/h.
