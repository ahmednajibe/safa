Design review round for F:\sound_system before building milestone M7 (Separate voice and music). Read docs/superpowers/plans/2026-11-15-m7-separation.md (the draft), docs/FEASIBILITY.md (real measurements of htdemucs and Kim_Vocal_2 on this GTX 1650 4 GB, audio-separator 0.30.2 quirks), docs/SPEC.md sections 2, 8, 9, 11, and backend/media/dfn.py + backend/media/chunks.py (how M6 did chunking and OOM fallback).

Load and use the skills that fit (brainstorming, systematic-debugging). You may read files; do NOT write or change files, no installs, never delete anything. Reply max ~450 words, bullets:
1. Model choice for SPEECH + background music (podcasts, videos with music beds, lectures): htdemucs vs Kim_Vocal_2 vs a MelBand/BS-Roformer vocals model available in audio-separator 0.30.2 — quality vs VRAM/speed on 4 GB; what should be the default and the 'speech-focused' option?
2. Instrumental/karaoke: sum of Demucs non-vocal stems vs mix minus vocals vs a dedicated instrumental model — artifacts/phase issues.
3. Chunking long files with audio-separator (it loads whole files): window size/overlap/crossfade, and the 44.1 kHz resample round trip (quality, length exactness).
4. Anything wrong or missing in the draft (download progress hooks, ONNX OOM detection, CPU forcing, mono handling, video handling, order relative to denoise/dereverb/breaths).
5. Objective tests without real music recordings (how to build a fair speech+music fixture; metrics like SI-SDR on synthetic mixes).
