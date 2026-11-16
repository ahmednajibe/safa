**Skills Loaded:** design-taste-frontend, web-design-guidelines, make-interfaces-feel-better (typography, surfaces, animations), impeccable (operate.md, craft-floor.md), plus using-superpowers and brainstorming. Grounded in docs/SPEC.md and docs/PLAN.md.

**Design Read:** *Operate-mode desktop audio/video cleanup studio for creators and educators, with a tactile hardware-inspired dark aesthetic (`DESIGN_VARIANCE: 3`, `MOTION_INTENSITY: 3`, `VISUAL_DENSITY: 8`).*

---

### 1. Art Direction
* **Tactile Broadcast Utility:** Inspired by Teenage Engineering, FabFilter, and Descript. Matte anodized surfaces, crisp 1px structural hairlines, physical-feeling switches, and high-contrast metering. No glowing AI-purple gradients, floating glass panels, or marketing cards.
* **Dark Mode Default (Locked):** Audio and video editing rely on color-coded waveforms, cut-region overlays, and video framing. A deep neutral dark surface (`#0C0D10`) prevents eye fatigue during long sessions, makes waveform transients pop at lower luminance, and matches [SPEC.md](file:///F:/sound_system/docs/SPEC.md#L62-L70) ("Single-page dark studio").

---

### 2. Design Tokens
* **Colors:**
  * `bg`: `#0C0D10` (root canvas)
  * `surface`: `#13151A` (panels/rack), `surface-elevated`: `#1A1D24` (active card/inputs), `surface- recessed`: `#08090B` (waveform well)
  * `border`: `#242832` (hairline structure), `border-focus`: `#F59E0B`
  * `text`: `#F1F3F7` (primary), `muted`: `#8E95A5` (labels/descriptions, >=4.8:1 contrast on surface)
  * `accent`: `#F59E0B` (Amber signal LED for active ops, primary CTA, playhead)
  * `success`: `#10B981`, `warn`: `#F59E0B` (CPU fallback notice), `error`: `#EF4444`
  * **Waveforms:** `wave-original`: `#64748B` (unprocessed slate), `wave-processed`: `#14B8A6` (clean teal), `wave-cut`: `rgba(239, 68, 68, 0.20)` with `#EF4444` top tick marks, `wave-preview`: `rgba(245, 158, 11, 0.16)` with `#F59E0B` drag handles.
* **Typography:**
  * Sans: `'Plus Jakarta Sans'` or `'Geist'` (system fallback: `'Segoe UI', sans-serif`), root `antialiased`.
  * Mono: `'JetBrains Mono'` with `tabular-nums` for timecodes, LUFS, Hz, ETA, VRAM, and percentages.
  * Scale (fixed `rem`): Title `1.125rem` (18px/600), Section/Op Title `0.875rem` (14px/600), Body/Control `0.8125rem` (13px/400), Meta/Badge `0.75rem` (12px/500).
* **Spacing, Radius, Shadows, Motion:**
  * Spacing: 4px base (`gap-1` 4px, `gap-2` 8px, `p-3` 12px, `p-4` 16px).
  * Concentric Radii: Outer panels `rounded-xl` (12px) with `p-1` (4px) -> inner cards `rounded-lg` (8px) -> controls/chips `rounded-md` (6px).
  * Shadows: `0 0 0 1px rgba(255,255,255,0.06)` on raised controls; no blurry drop shadows.
  * Motion: `150ms cubic-bezier(0.2, 0, 0, 1)` on explicit properties (`transition-[transform,opacity,background-color,box-shadow]`). Never `transition: all`. Button press uses `active:scale-[0.96]`.

---

### 3. Layout (One-Screen Workbench vs. Steps)
**Decision:** **One-screen split workbench** (no multi-step wizard). Users must inspect the waveform, tweak parameters, preview a 15-30s region, and A/B compare without losing context.

* **Desktop (`1280px-1920px`, `h-dvh overflow-hidden`):**
  * **Top Bar (`48px`):** App title, Hardware Health pill (`GTX 1650 4GB · CUDA Ready`), and **History Drawer** trigger (slide-over `<dialog>` with badge count).
  * **Left Column (`420px-480px`, scrollable):**
    1. Compact **Media Inspector + Track Picker** (or collapsible Drop Zone if replacing file).
    2. Sticky **Preset Chips** strip.
    3. **Processing Rack**: Operation cards stacked in fixed execution `order` (`10` to `90`), numbered `01`-`14` so users see the exact signal chain.
  * **Right Column (`1fr`, flex-col):**
    1. **Video Viewport** (collapsible; shown only when media has video stream, max `38vh`).
    2. **Waveform & Transport Well** (`wavesurfer.js`, time ruler, zoom, play/pause, draggable 15-30s preview region + "Preview Region" button, A/B switch).
    3. **Bottom Dock (`88px`):** Output format picker (`WAV`, `FLAC`, `MP3 320k`, `AAC`, `Keep Video`) + Primary CTA ("Run Cleanup") -> transforms in-place into **Stage Stepper** during runs and **Download Bar** on completion.
* **Narrow Fallback (`<1024px`):** Single-column scroll (`min-h-dvh`). Sticky top waveform/transport bar (`160px`), followed by Presets, Processing Rack, and sticky bottom Run/Progress/Download bar.

---

### 4. Key Components & States
* **Drop Zone:**
  * *Idle:* Full right-pane well before upload; dashed `#242832` border, supported formats (`WAV, MP3, FLAC, MP4, MKV`), sample fixture button.
  * *Drag-over:* `#F59E0B` border, `bg-amber-500/5`.
  * *Uploading/Probing:* Determinate byte bar + skeleton inspector chips.
  * *Error:* Inline `#EF4444` banner with recovery ("File has no readable audio stream. Try another file.").
* **Operation Card (Rack Unit):**
  * *Off:* Compact single row (`44px` height), toggle switch off, title, one-line summary, hardware badge (`GPU` in subtle emerald or `CPU` in neutral).
  * *On:* Left `2px` `#F59E0B` indicator bar, surface `#1A1D24`, expands smoothly to show 1-3 inline controls (sliders/selects with labels above inputs).
  * *Disabled-by-conflict:* Opacity `50%`, toggle locked off, inline lock pill: *"Disabled: Remove Music is set to Instrumental (Karaoke)."*
  * *Unavailable-on-this-machine:* Opacity `45%`, badge `Model Offline`, reason: *"Requires first-run model download or optional environment."*
* **Preset Chips:** Horizontal pills (`Podcast Cleanup`, `Remove Background Music`, `Fix Echoey Room`, `Lecture`, `Karaoke`, `Video Jump-Cut`). Clicking toggles matching ops and scrolls the rack; manual edits switch chip state to `Custom`.
* **Stage Stepper:** Shows active stage (`Stage 3 of 5: Reduce Background Noise`), determinate bar (or shimmer stripe when library gives no %), `tabular-nums` % + ETA (`00:42 left`), live `GPU / CPU Fallback` badge, and red ghost `Cancel` button (kills worker tree).
* **A/B Toggle (`Tab` hotkey):** Segmented switch (`Original [A]` in `#64748B` vs `Cleaned [B]` in `#14B8A6`). Swaps audio buffer and waveform color while translating playhead seamlessly through `timeline.py` segment map.
* **Cut-Region Display:** Kept speech segments stay bright; cut pauses/trimmed ends render with `wave-cut` red tint and top markers so video/audio jump-cuts are visually obvious before and after rendering.
* **Download Buttons & Empty/Error States:** Primary download button for main output + secondary pills for extras (`SRT`, `VTT`, `Vocals.wav`, `Instrumental.wav`). History empty state: *"No past jobs yet. Processed files stay here for 7 days."* OOM warning: *"GPU memory full (4 GB limit). Retrying Voice Separation on CPU."*

---

### 5. Microcopy (SPEC §2 Operations in Execution Order)

| Order | ID | Plain-Language Name | One-Line Description (Non-Expert Friendly) |
|---|---|---|---|
| 10 | `trim` | **Trim Start & End** | Cut unwanted dead air from the very beginning or end of your recording. |
| 15 | `channel` | **Fix Stereo Channels** | Convert to mono or rescue a mic recorded on only the left or right side. |
| 20 | `highpass` | **Remove Low Rumble** | Filter out deep microphone thumps, desk bumps, and air-conditioner vibration. |
| 25 | `dehum` | **Remove Electrical Hum** | Erase steady 50 Hz or 60 Hz power-line buzz and its overtones. |
| 30 | `separate` | **Separate Voice & Music** | Strip background music to keep only speech, or remove vocals for karaoke. |
| 35 | `dereverb` | **Reduce Room Echo** | Dry up hollow, boxy sound from untreated rooms or distant microphones. |
| 40 | `denoise` | **Reduce Background Noise** | Clean out steady fan hiss, traffic, and room noise while keeping speech natural. |
| 45 | `declick` | **Remove Clicks & Pops** | Smooth out sharp mouth clicks, lip smacks, and digital crackles. |
| 50 | `enhance` | **Studio Voice Polish** | Balance tone, tame harsh "S" sounds, and even out quiet and loud words. |
| 55 | `enhance_ai` | **AI Voice Restoration** | Rebuild muffled or heavily degraded speech using generative AI (experimental). |
| 60 | `silence` | **Shorten Long Pauses** | Automatically tighten dead air and awkward silences in audio and video. |
| 70 | `speed` | **Change Playback Speed** | Speed up or slow down the recording without making voices sound chipmunk-pitched. |
| 80 | `loudness` | **Normalize Volume** | Set consistent broadcast or YouTube volume so listeners never reach for the dial. |
| 90 | `subtitles` | **Generate Subtitles** | Transcribe the final edited audio into synced SRT and VTT caption files. |

---

### 6. Accessibility & Keyboard
* **Shortcuts (disabled when typing in inputs):** `Space` Play/Pause, `Tab` toggle A/B comparison (when focus is in player/workbench; standard focus traversal via `Shift+Tab` or when inside forms, or dedicated `A` / `B` keys + clickable `Tab` pill), `P` play 15-30s preview region, `[` / `]` nudge preview region handles by 0.5s, `Esc` cancel modal/drawer.
* **Focus & Hit Areas:** `2px solid #F59E0B` with `outline-offset: 2px` on `:focus-visible`. All icon buttons and toggles enforce `min-h-10 min-w-10` (40x40px desktop, 44x44px touch via `after:` pseudo-element).
* **Reduced Motion & Contrast:** `@media (prefers-reduced-motion: reduce)` disables rack height animations and shimmer stripes, keeping instant state/color cues. All text and form borders meet WCAG AA (4.5:1 text, 3:1 controls).

---

### 7. Top 5 UI Mistakes to Avoid
1. **Allowing drag-to-reorder on operation cards:** Backend enforces fixed signal-chain `order` ([SPEC.md](file:///F:/sound_system/docs/SPEC.md#L32)); letting users drag cards creates false expectations. Display cards in fixed pipeline order with clear sequence numbers.
2. **Resetting the playhead on A/B switch (or ignoring timeline cuts):** If `trim`, `silence`, or `speed` ran, output duration differs from source duration. A/B toggling must map timestamps via `timeline.py` so the listener hears the exact same spoken word.
3. **Hiding disabled operations instead of explaining conflicts:** When Karaoke mode disables `denoise`/`enhance`/`silence`, hiding those cards confuses users. Keep them visible in rack order with an inline conflict badge.
4. **Proportional numerals in live progress/timecodes:** Without `tabular-nums`, rapidly updating milliseconds, LUFS, and percentages jitter horizontally on every SSE tick.
5. **Exposing raw DSP jargon as primary labels:** Controls labeled `atten_lim_db`, `bandreject`, or `-16 LUFS / -1 dBTP` alienate non-experts. Lead with plain outcomes ("Noise Reduction Strength", "Target Volume: Podcast / YouTube") and show technical units as secondary readouts.
