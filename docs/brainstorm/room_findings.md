# Room echo / fan on Recording (3) — findings (2026-11-15)

Round: Claude + Astra (`astra_room.md`) + Argon (`argon_room.md`). Experiments by the orchestrator: `.orchestrator/analysis/try_dfn.py`, `try_dereverb.py`, `try_wpe.py`, `try_wpe_strong.py`; listening files in `data/review/experiments/`.

| variant | SRMR proxy ↑ | env std ↑ | LTAS ripple 220–1000 Hz ↓ |
|---|---|---|---|
| A original | 9.82 | 6.87 | 1.43 |
| B DFN 24 dB | 11.33 | 8.39 | 1.53 |
| E DFN → WPE (10 taps) | 11.39 | 8.48 | 1.46 |
| E DFN → WPE (40 taps) | 11.48 | 8.57 | 1.62 |
| F DFN → WPE → auto-resonance EQ | 11.10 | 8.06 | **1.15** (cuts 621/516/281 Hz) |

Conclusions
- Music dereverb models (UVR-DeEcho-DeReverb, MelBand-Roformer anvuew) removed almost nothing (reverb stems −56/−61 dBFS vs −26.6 dry) — small-room speech coloration is out of their training domain. Not used for speech.
- DFN3 alone gives most of the syllabic-clarity gain (it also dereverbs partially). WPE adds little here: the "bathroom" sound is mostly early reflections / room modes, not late tail.
- Auto-resonance notch EQ measurably reduces boxiness (ripple −25 %).
- Experiment bug found by Astra: separator outputs 44.1 kHz were fed to 48 kHz DFN in try_dereverb.py (variant D invalid).

Decision (pending the user's listening):
- `dereverb` op ("Reduce room echo") = **auto-resonance EQ (220–1000 Hz, ≤3 cuts, −2…−6 dB, Q 4) + light WPE**, CPU, numpy only, runs **after** denoise (order change: denoise before dereverb).
- Next candidate for a stronger "AI" option: ClearerVoice MossFormer2_SE_48K (joint denoise+dereverb, Apache-2.0) — test after M5 lands (needs a dependency install).
