Problem-solving round for F:\sound_system (local audio cleanup app; read docs/SPEC.md sections 2, 8 and docs/FEASIBILITY.md for the installed stack: ffmpeg 7.1, pedalboard 0.9.16, numpy/scipy, DeepFilterNet3, silero-vad, torch CPU/GPU on a GTX 1650).

The user listened to their own speech recording (laptop/phone voice memo, m4a 48 kHz, about -36 LUFS, very clean -82 dBFS room floor) after our M4a chain (mono, highpass 75 Hz, dehum 50 Hz, ffmpeg adeclick default, pedalboard voice EQ + compressor thr -18 ratio 3 + ffmpeg deesser, 2-pass loudnorm to -16 LUFS / -1 dBTP) and said:
(a) I can hear my saliva (mouth clicks / lip smacks / wet mouth noises)
(b) when the volume was raised, the sound has something like a bass under it.

Our measurements (original -> processed): quiet-part floor -81.8 -> -59.5 dBFS; speech-to-floor 50.7 -> 46.4 dB; 80-200 Hz share of the quiet parts -8.6 -> -6.2 dB; transient click count 439 -> 436 (adeclick removed nothing).

Load and use the skills that fit (brainstorming, systematic-debugging). Do NOT write or change files, no installs, never delete anything. Reply max ~450 words, bullet points:
1. Best practical way to remove mouth clicks/saliva noises with what we have installed (or one small pip-installable, Windows, Python 3.11, offline library): algorithm, exact ffmpeg filter params or a numpy/scipy approach (detection features: duration, band, crest factor; repair: interpolation/spectral), and how to avoid damaging consonants like t/k/p.
2. The bass/boom: proximity/boxiness vs raised floor — which EQ moves (frequencies, gains, Q) and whether a downward expander / gate before the compressor is right (thresholds relative to the measured floor), and order in the chain.
3. Should noise reduction (DeepFilterNet) be on by default in the Podcast preset, and where in the order relative to the expander and compressor?
4. An objective test we can run on the user's recordings to prove each fix (metric + pass threshold).
