# UI Design — Sound System

Merged from the design round: Claude (design-taste-frontend, app-screen run: style `true-black-flat`, seed 1106272552, sheet in `.taste-skill/direction.json`), Astra (`docs/brainstorm/astra_ui.md`) and Argon (`docs/brainstorm/argon_ui.md`), all using design-taste-frontend, web-design-guidelines, make-interfaces-feel-better and impeccable. Where they disagreed, the decision is marked **Decided**.

## 1. The screen's one question
**"What will happen to my recording, and does it sound better?"**
- Biggest thing: the recording itself (video if present + waveform with original/processed comparison).
- First job, one click away: **Process** (after picking a preset or steps). Second: **Preview** a 15–30 s region.
- Finding line under the title: the media facts that matter (e.g. `Interview final.mp4 · 42:18 · stereo 48 kHz · −23.4 LUFS`).

## 2. Art direction
A quiet, dark audio workbench. Flat true-black surfaces, the recording is the centrepiece, colour exists only on data (the processed waveform). Approachable plain language over audio jargon; technical units appear as secondary readouts.

**Face (what makes it this product):** the one data hue is **VU-meter amber `#f2b33d`** (the colour of a tape machine's meter) on the processed waveform. The **signal chain**: operation steps stacked on one spine in the order they run, like a rack of studio units.

**Dark by default** (all three agreed): long sessions, video review, waveform contrast. No light theme in v1.

## 3. Tokens (Tailwind theme + CSS variables in `frontend/src/styles/tokens.css`)
```css
:root {
  --ground: #000000;     /* page, top bar, dock: one ground */
  --surface: #111111;    /* panels: ops rack, player panel */
  --raised: #2a2a2a;     /* nested: expanded op body, inputs, chips */
  --well: #0a0a0a;       /* waveform well (one step darker than surface) */
  --rule: #1a1a1a;       /* the only 1px lines: list dividers inside panels, sticky bar when content scrolls under */
  --control: #444444;    /* control outlines (inputs, unchecked switch track) */
  --text: #ffffff;
  --muted: #b4b4b4;      /* secondary text */
  --faint: #949494;      /* meta (≥4.5:1 on --raised) */
  --data: #f2b33d;       /* the ONE hue: processed waveform, processed progress fill */
  --wave-original: #6b6b6b;
  --negative: #e5484d;   --pill-negative-bg: #2c1a20; --pill-negative-ink: #e5566f;
  --warning: #e5a93b;    --pill-warning-bg: #2b2414;  --pill-warning-ink: #e5a93b;
  --positive: #7fd18b;   --pill-positive-bg: #17261a; --pill-positive-ink: #8fdc9a;
  --pill-neutral-bg: #222222; --pill-neutral-ink: #ffffff;
  --cut-fill: rgba(229, 72, 77, 0.14);  /* removed spans, drawn with 45° hatch */
  --preview-fill: rgba(255,255,255,0.06); --preview-edge: #ffffff;
  --radius-panel: 20px; --radius-inner: 12px; --radius-control: 999px; --radius-tag: 6px;
  --gap: 14px; --pad: 22px; --control-h: 36px;
  --fs-title: 20px; --fs-section: 16px; --fs-figure: 24px; --fs-body: 14px; --fs-small: 12px;
  --ease: cubic-bezier(.2, 0, 0, 1); --t-feedback: 120ms; --t-disclose: 180ms;
}
```
Rules:
- **Elevation by lightness only.** No borders on panels, **no shadows at rest** (only menus/dialogs/drawer: `0 8px 24px #0009`). No gradients, glows, glass, blur.
- **The amber never touches buttons, toggles, tabs, nav or focus rings.** Primary action = **white 36px pill, black text** (inversion). Secondary = `--raised` pill, white text. Destructive = ghost with `--negative` text.
- **Focus ring:** 2px solid `#ffffff`, offset 2px, `:focus-visible` only.
- Status uses pill pairs only (pale bg + same-hue ink). One status mark per row.

**Decided** (Argon proposed amber CTA + teal processed + slate original; Astra blue accent): one hue only, per the style's ≤3% colour budget; Argon's two data hues and Astra's blue-on-buttons are both off-style.

## 4. Type
- **Inter** 400/500/600, **bundled locally via `@fontsource/inter`** (app must work offline — no Google Fonts link). Fallback `system-ui, "Segoe UI", sans-serif`.
- Sizes only: 20 (title / h1), 16 (section, op title), 24 (figures like LUFS, duration in inspector), 14 (body/controls), 12 (meta). Nothing above 600 weight.
- `font-variant-numeric: tabular-nums` on every timecode, %, ETA, LUFS, Hz, dB, file size, anything SSE updates.
- **No monospace** (Decided over Argon's JetBrains Mono): tabular Inter does the job.
- Sentence case everywhere; no uppercase tracked labels; no eyebrows.
- Filenames rendered with `dir="auto"` + `unicode-bidi: isolate` (Arabic names).

## 5. Layout — one persistent workbench (all agreed; no wizard)
Desktop 1280–1920 px, `h-dvh`, page doesn't scroll; columns scroll internally.

```
┌ top bar 48px (ground) ─────────────────────────────────────────────────────────┐
│ [mark] Sound System   Interview final.mp4 ▾        GTX 1650 · GPU ready   History│
├──────────────┬─────────────────────────────────────────────────────────────────┤
│ OPS RACK     │ PLAYER PANEL                                                     │
│ 400px        │  h1: filename   finding line (duration · channels · rate · LUFS) │
│ surface      │  [video viewport, max 40vh, only if video]                       │
│ presets row  │  [waveform well: ruler, original+processed, cut hatching,         │
│ ─ spine ─    │   preview region]  zoom − +                                       │
│ op card      │  transport: ⏵ 00:12.40 / 42:18.00   [Original | Processed]  A/B  │
│ op card      │  cut list (text) when timeline ops ran                            │
│ …            │                                                                   │
├──────────────┴─────────────────────────────────────────────────────────────────┤
│ DOCK 72px (ground): output [WAV▾] [☑ keep video]   Preview region   [ Process ]  │
│ → during run: stage stepper + progress + ETA + device + Cancel                   │
│ → after run: Download MP4 · WAV · SRT · stems                                    │
└────────────────────────────────────────────────────────────────────────────────┘
```
- Grid: `400px minmax(0,1fr)`, gap `--gap` (14), outer margin 14. Panels `--surface`, radius 20, padding 22.
- Before upload, the player panel *is* the drop zone (full panel), the rack shows presets and ops greyed with "Add a file to start".
- **Narrow (<1024px):** single column: player (sticky waveform 160px) → presets → ops rack (collapsed to a summary "5 steps on · Edit") → sticky dock. History = full-width drawer. 16px margins.

## 6. Components & states

### Drop zone (player panel before upload)
- Idle: 20px-radius `--well` field, centred: "Drop audio or video here" (16/600) + "Choose file" secondary pill + meta line "WAV, MP3, FLAC, M4A, MP4, MKV, MOV · stays on this computer".
- Drag-over: field lightens to `--raised`, text "Release to add".
- Uploading: filename, bytes + %, determinate bar (white fill), Cancel. Then "Reading file…" while probing.
- Error (inline, negative pill + sentence): "This video has no audio track. Choose another file." / "This file type isn't supported. Try WAV, MP3, MP4…" / "Not enough disk space: needs about 3.2 GB, 1.1 GB free."

### Media inspector (top of player panel after upload)
h1 = filename; finding line in `--muted`; audio-track select if >1 stream; "Not measured" for unknown values. "Replace file" text button.

### Presets row (top of rack)
Chips: Podcast cleanup · Remove background music · Fix echoey room · Lecture · Karaoke · Video jump-cut. Selected = white fill black text + check; manual edits → chip "Custom" (selected). Applying a preset with unavailable steps: inline note "Room echo isn't available on this computer; the rest was applied."

### Ops rack — the signal chain
- Cards stacked in backend `order`, joined by a 1px `--rule` spine at the left (12px from card edge) — order shown by position, **no 01/02 numbering** (Decided over Argon; house rule). Rack header: "Steps run top to bottom".
- **Off:** 52px row: switch, name (14/600), one-line description (12, `--faint`), device tag right ("GPU" / "CPU", neutral pill).
- **On:** switch on (white track, black knob), card `--raised`, expands (180ms height + opacity) to 1–3 labelled controls with plain labels and units as secondary ("Strength · 24 dB"), "Advanced" disclosure for the rest, "Reset" text button.
- **Disabled by conflict:** stays in place, 50% content opacity, switch locked, warning pill + reason: "Off while Karaoke is on: noise reduction works on speech."
- **Unavailable:** three distinct reasons — "Not available on this computer", "Model not downloaded yet — connect to the internet once", "Coming later". Neutral pill, switch disabled.
- Denoiser engine = radio group inside the denoise card (AI / Classic).
- Device tag shows expected device before run (from the dock's "Process on" choice and the step's engine; CPU-only steps show "Processor" with no warning) and actual during run; CPU fallback shows a persistent warning pill + sentence "Graphics memory ran out. Running this step on the processor instead."

### Waveform well (wavesurfer.js, peaks from backend)
- Original waveform `--wave-original`; processed waveform `--data` amber. Before processing, only original. After, **Decided:** A/B switch swaps which waveform is in front (the other drawn at 35% behind) and which audio plays.
- Cut/removed spans: `--cut-fill` + 45° hatch (pattern, not colour alone) and a "Removed 0:03.2" label on hover/focus; text cut list below the transport.
- Preview region: white 1px edges, `--preview-fill`, draggable handles + numeric start/end fields (alternative to dragging). Changing settings after a preview → "Preview out of date" neutral pill.
- Playhead: 1px white line. Ruler: 12px `--faint` tabular labels, 4–8 labels, never rotated.

### Transport + A/B
Play/pause (40px round raised), timecode `00:12.40 / 42:18.00` tabular, segmented control **Original | Processed** (selected = white fill black text). Switching keeps the playhead, translated through the timeline map; inside a removed span it jumps to the next kept boundary and announces "Jumped to 0:41.2 — this part was removed".

### Dock
- Idle: output format select, "Keep video" checkbox (video only), **"Process on" segmented control (labelled radio group): Graphics card | Processor** (shown only when a GPU is usable; otherwise the top bar says "CPU only" and no control is shown; choice remembered in localStorage), "Preview region" secondary, **Process** primary (disabled with a visible reason until ≥1 step on). Under Process, an always-visible subline (12px, `--muted`, tabular): Graphics card → "Uses GTX 1650 · about 2 min · switches to the processor if graphics memory runs out"; Processor → "Uses the processor · about 10 min · graphics card stays free" (bucket from `/api/estimate`). No hover-only explanations.
- Running: stage stepper "Step 3 of 5 · Reduce background noise", current-stage bar + overall %, ETA ("About 4 min left"; unknown → "Estimating…"; no % → "Working…" with indeterminate bar), device pill, "Cancel" ghost-negative. Queued → "Waiting for the current job to finish". Model download → "Downloading voice model (first time only) · 45%".
- Done: primary "Download MP4" (or main audio), secondary pills for WAV/FLAC/MP3, SRT, VTT, Vocals, Music. Failed/interrupted: negative pill + one-line reason + "Retry" (settings kept).

### History drawer
Right drawer 420px: rows 56px (filename dir=auto, date in words, state pill, size), actions: re-download, delete (confirm). Expiry "Kept until 21 Nov". Empty: "Your processed recordings will appear here."

### Top bar
Mark (simple amber-free white monogram: a waveform-in-square glyph, 20px) + "Sound System" 16/600; current file name; hardware pill "GTX 1650 · GPU ready" / "CPU only"; "History" text button with count of jobs needing attention only.

## 7. Operation names (UI copy)
| id | Name | Description |
|---|---|---|
| trim | Trim ends | Keep only the part between a start and end time. |
| channel | Fix channels | Keep stereo, combine to mono, or use only the left or right side. |
| highpass | Remove low rumble | Cuts deep thumps, desk bumps and air-conditioner rumble. |
| dehum | Remove electrical hum | Removes steady 50/60 Hz buzz from power lines and equipment. |
| separate | Separate voice and music | Keep the voice, keep the music, or export both. |
| dereverb | Reduce room echo | Dries up the hollow sound of big or bare rooms. |
| denoise | Reduce background noise | Lowers hiss, fans, traffic and room noise while keeping speech natural. |
| declick | Remove clicks and pops | Smooths mouth clicks, crackles and sharp ticks. |
| enhance | Polish voice | Balances tone, softens harsh "s" sounds and evens out loud and quiet words. |
| enhance_ai | Restore damaged voice | Tries to rebuild muffled or badly recorded speech (experimental). |
| silence | Shorten pauses | Tightens long gaps while leaving natural breathing room. |
| speed | Change speed | Faster or slower without the chipmunk effect. |
| loudness | Even out volume | Sets a steady listening level (Podcast −16 / YouTube −14) and stops clipping. |
| subtitles | Create subtitles | Writes timed captions (SRT/VTT) from the final audio. |

Param labels lead with outcome: "Strength", "Hum frequency 50 Hz / 60 Hz", "Longest pause to keep", "Target: Podcast / YouTube / Custom", with the technical value as a secondary readout.

## 8. Keyboard & accessibility
- **Decided (A/B hotkey):** Tab stays normal focus navigation (Astra + web-design-guidelines). Shortcuts, disabled while typing: `Space` play/pause, `A` original, `B` processed (also toggles), `←/→` seek 5 s, `P` play preview region, `[`/`]` nudge region 0.5 s, `Esc` close drawer/menu. Shortcuts listed in a `?` popover.
- 40px hit targets desktop, 44px touch. Contrast AA (text 4.5:1, controls 3:1). Colour never the only signal (hatch for cuts, labels on pills).
- `prefers-reduced-motion`: no height animation, no indeterminate sweep (static striped bar instead).
- Live region (polite) announces stage changes and completion.
- All controls are real `<button>`/`<input>`/`<select>` with labels.

## 9. Motion
Feedback only (app screens perform no entrances): press `scale(0.97)` 120ms, card expand 180ms, drawer slide 180ms, progress fill transitions 200ms linear. Transform/opacity/background-color only; never `transition: all`.

## 10. Mistakes to avoid (from all three)
1. Drag-to-reorder ops (backend order is fixed).
2. A/B that resets or misaligns the playhead after cuts/speed.
3. Hiding conflicts, unavailable ops or CPU fallback.
4. Invented progress % or precise ETAs.
5. Proportional digits in live numbers (jitter).
6. Raw jargon as primary labels (`atten_lim_db`, `bandreject`).
7. Colour or dragging as the only way to understand or control something.

## 11. Verification for M9a/M9b
- `node <design-taste-frontend>/scripts/lint.mjs --app frontend/src` and `inspect.mjs --app` on the production build at 1440/768/390: 0 FAIL.
- web-design-guidelines review of the UI code.
- Browser walkthrough (webapp-testing): upload → preset → preview → process → A/B → download, keyboard-only pass, Arabic filename.
