Context loading did not run; I’ll read the existing project context directly. Impeccable’s launcher can create cache files, so I’m using its written guidance to honor your read-only request.Loaded **design-taste-frontend** (the installed `.claude` app-screen guidance), **web-design-guidelines** (fresh rules), **make-interfaces-feel-better**, and **impeccable** (Shape/Operate guidance), plus brainstorming and using-superpowers. Read SPEC and PLAN, including the requested sections. No project files changed or installs run.

**1. Art direction.** A quiet audio workbench: charcoal surfaces, precise time rulers, readable controls, and the recording itself as the visual centerpiece. Default **dark**, matching SPEC §6 and reducing surrounding brightness during video review. Give it identity through the original/processed waveform comparison and clearly marked edits. Use flat panels, restrained outline icons, and plain language.

**2. Design tokens.** Expose semantic CSS variables through Tailwind.

| Role | Value |
|---|---|
| Background / surface / raised | `#141414` / `#1E1E1E` / `#282828` |
| Structural border / control outline | `#383838` / `#767676` |
| Text / muted text | `#F2F0EB` / `#A8A8A8` |
| Accent / focus | `#8AB7D8` |
| Success / warning / error | `#8CCB9A` / `#E7BE70` / `#F18A8A` |
| Original / processed waveform | `#A8A8A8` / `#8AB7D8` |

Use dark text on accent-filled buttons. Typography: `"Segoe UI Variable", "Segoe UI", system-ui, sans-serif`; works offline. Sizes/line-heights: metadata 12/16, body 14/20, section labels 16/24, title 24/32; weights 400/600. Timecodes, percentages and ETA use tabular numerals.

Spacing: 4/8/12/16/24/32px. Radius: tags 4px, controls 6px, panels 8px. Panels have no shadow; overlays use `0 8px 24px #0006`. Motion: 120ms feedback, 180ms disclosure, `cubic-bezier(.2,0,0,1)`; A/B switching is immediate.

**3. Layout.** Choose **one persistent workspace**, with progressive disclosure.

Desktop: 56px header containing Sound System, local-processing status, and History. Below, use 24px gutters, maximum width 1760px, and a `344px minmax(0,1fr)` grid with 24px gap.

The left column contains upload/media inspector, presets, and a scrolling operation list. The right contains video when applicable, waveform, preview selection, transport/A/B, progress, then results. Keep Preview and Process full file beside output settings, always reachable.

After upload, replace the large drop zone with filename, duration, channels, sample rate, codec, resolution, measured LUFS and audio-track picker. Unmeasured values display “Not measured.”

Below 1024px, stack sections; collapse operations into an expandable summary. Use 16px gutters and a sticky action bar with reserved space beneath content. History opens as a drawer, full-width on narrow screens.

**4. Components and states.**

- **Drop zone:** idle “Drop audio or video here” plus Choose file; drag-over strengthens outline and says “Release to add”; uploading shows filename, bytes/percentage and Cancel; errors stay inline: “This video has no audio track. Choose another file.”
- **Operations:** compact cards in backend execution order. Off retains a readable description; On reveals 1–3 labeled controls with units and reset. Use schema-driven fields: 50/60Hz selector, noise strength, pause duration, loudness target. Advanced settings disclose separately.
- **Conflicts:** preserve requested settings while showing effective exclusions. “Unavailable with Karaoke: noise reduction works on speech.” Karaoke excludes denoise, voice polish and speech-based silence cutting; amplitude mode remains eligible. Denoiser engines use one radio group.
- **Availability:** distinguish “Not available on this machine,” “Model missing—connect once to download,” and “Planned feature.” Room echo depends on feasibility; AI restoration stays unavailable until implemented. GPU/CPU badges show expected device before running and actual device during processing. “GPU memory filled; continuing on CPU.”
- **Presets:** Podcast cleanup, Remove background music, Fix echoey room, Lecture, Karaoke, Video jump-cut. Neutral selected fill plus checkmark; edits change the label to Custom. Explain unavailable ingredients before applying.
- **Preview:** draggable 15–30-second region with editable start/end fields. Changed settings mark the previous preview “Out of date.”
- **Progress:** ordered stages, current-stage and overall progress, approximate ETA, device and Cancel. Use SSE values; unknown progress says “Working…” and ETA “Estimating…”. Include model downloads and queued state. Interrupted/failed jobs retain settings and offer Retry.
- **Results:** Original/Processed control, synchronized video, and labeled cut hatching. Expose §8 timeline segments to translate playheads across trimming, cuts and speed; inside a removed span, move to the next retained boundary and announce it. Provide a textual cut list.
- **Downloads/history:** select WAV/FLAC/MP3/AAC before processing; offer only generated audio, MP4, SRT/VTT and stem files. History shows state, re-download, expiry and confirmed Delete. Empty copy: “Your processed recordings will appear here.”

**5. Operation microcopy.**

| Operation | Name and description |
|---|---|
| `trim` | **Trim ends** — Keep the part between your chosen start and end. |
| `channel` | **Choose audio channels** — Keep stereo, combine to mono, or use one side. |
| `highpass` | **Remove low rumble** — Reduce deep vibrations and microphone handling noise. |
| `dehum` | **Remove electrical hum** — Reduce steady buzzing from power or equipment. |
| `separate` | **Separate voice and music** — Keep voice, keep music, or export both. |
| `dereverb` | **Reduce room echo** — Soften the hollow sound of reflective rooms. |
| `denoise` | **Reduce background noise** — Lower hiss, fans and steady background noise. |
| `declick` | **Remove clicks and pops** — Reduce brief crackles and sharp ticks. |
| `enhance` | **Polish voice** — Balance tone, soften harsh “s” sounds and smooth volume. |
| `enhance_ai` | **Restore damaged voice** — Try to improve muffled or degraded speech. |
| `silence` | **Shorten pauses** — Reduce long gaps while leaving breathing room. |
| `speed` | **Change playback speed** — Speed up or slow down without changing pitch. |
| `loudness` | **Set consistent volume** — Reach a listening-level target while limiting peaks. |
| `subtitles` | **Create subtitles** — Transcribe the edited recording into timed captions. |

**6. Accessibility and keyboard.** Use semantic controls, visible 2px focus rings, 40px desktop/44px touch targets, 4.5:1 text and 3:1 control contrast. Pair color with labels. Player-scoped Space plays; arrows seek. **Tab toggles A/B only in explicitly enabled comparison mode; Escape exits.** Normal Tab navigation remains elsewhere. Provide numeric alternatives to dragging, polite stage announcements, reduced-motion behavior, and bidirectional isolation for Arabic filenames.

**7. Five mistakes to avoid.**

1. Hiding conflicts or CPU fallback.
2. Inventing progress percentages or precise ETAs.
3. Misaligning A/B after timeline edits.
4. Exposing every technical parameter immediately.
5. Treating waveform color or dragging as the only explanation or control.
