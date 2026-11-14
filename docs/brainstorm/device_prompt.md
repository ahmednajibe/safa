Design review round for F:\sound_system (local audio/video cleanup web app, Windows, GTX 1650 4 GB).

The user asked: can the user choose CPU or GPU for processing? Claude (orchestrator) drafted the answer. Read:
- docs/SPEC.md section 9 (Processing device choice) and section 8 (VRAM / OOM rules)
- docs/UI_DESIGN.md (search for Run on) — the dock control and per-step override
- docs/superpowers/plans/2026-11-15-m3a-media-storage.md (search for resolve_device and run_on)
- docs/FEASIBILITY.md (real CPU vs GPU timings and VRAM on this machine)

Load and use the skills that fit: brainstorming, design-taste-frontend (app-screen rules), web-design-guidelines, make-interfaces-feel-better.

Do NOT write or change any files, do not run installs, never delete anything. Reply with a critique only, max ~400 words, bullet points:
1. Is auto / gpu / cpu at job level + per-step run_on override the right model? Too much, too little, anything missing (e.g. what happens when GPU mode is chosen but a model only runs on CPU, ONNX vs torch vs CTranslate2 differences, a running job when the user changes the setting, remembering the choice between sessions)?
2. Is retrying on CPU after OOM correct when the user explicitly chose GPU, or should it fail / ask?
3. UI: is a segmented control in the dock the right place and wording for non-experts? Should the per-step override exist at all?
4. Should the UI show estimated time per device (we have real timings)? How, without inventing precise ETAs?
5. Concrete changes you recommend (exact wording / behaviour).
