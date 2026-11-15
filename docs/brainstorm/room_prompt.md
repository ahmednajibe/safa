Problem-solving round for F:\sound_system (local audio cleanup app). Installed stack: see docs/FEASIBILITY.md (Windows 11, Python 3.11 via uv, torch 2.5.1+cu124, GTX 1650 4 GB, 16 GB RAM, DeepFilterNet3, audio-separator 0.30.2, pedalboard, ffmpeg 7.1).

The user's 30 s laptop voice memo (Recording (3), m4a 48 kHz) sounds like they are talking in a bathroom (hollow, boxy, echo) and has laptop fan noise. Orchestrator experiments (files in data/review/experiments, scripts in .orchestrator/analysis/try_dfn.py and try_dereverb.py):
- DeepFilterNet3 (atten 24 / 100): non-speech level -27.8 -> -30.8 dBFS (only 13% of the file is non-speech; speech is nearly continuous).
- audio-separator dereverb models UVR-DeEcho-DeReverb.pth and dereverb_mel_band_roformer_anvuew_sdr_19.1729.ckpt: their reverb stem was only -56 / -61 dBFS vs -26.6 dBFS dry, i.e. they removed almost nothing. Hypothesis: these music-vocal models target long tails; the user's problem is short dense early reflections / small-room coloration.

Load and use the skills that fit (brainstorming, systematic-debugging). You may read files; do NOT write or change files, no installs, never delete anything. Reply max ~450 words, bullets:
1. Is the hypothesis right? How to measure small-room reverb / early reflections objectively on a single mono recording without a clean reference (e.g. blind DRR / C50 / T60 estimation, spectral flatness of tails, modulation spectrum) — give one practical numpy/scipy metric.
2. Best open-source SPEECH dereverberation that runs offline on Windows + Python 3.11 + GTX 1650 4 GB (or CPU), ranked: e.g. nara_wpe (WPE), VoiceFixer, resemble-enhance, SGMSE+/StoRM, Miipher-style, DeepFilterNet variants, others you know. For each: install risk on Windows/Py3.11 with our pins (numpy 1.26, torch 2.5.1), quality on small-room speech, speed/VRAM, license.
3. A classic DSP fallback for boxiness/hollowness (EQ moves for small-room resonances, e.g. automatic resonance notch finding) that we could add cheaply.
4. Your recommendation for the app's 'Reduce room echo' step and its order relative to DeepFilterNet.
