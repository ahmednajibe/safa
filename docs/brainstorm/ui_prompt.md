UI/UX design round for the project in F:\sound_system (local audio/video cleanup web app). Read docs/SPEC.md sections 2, 5, 6 and 8 first.

BEFORE answering, load and follow your installed skills that fit this task: **design-taste-frontend** (use its app-screen / tool-UI guidance, not landing-page guidance), **web-design-guidelines**, **make-interfaces-feel-better**, and **impeccable** if available. Say which skills you actually loaded.

Do NOT write or change any files, do not run installs, never delete anything. Reply with a design proposal only (max ~900 words), concrete enough that a coding agent can build it with React + TypeScript + Tailwind + wavesurfer.js:

1. **Art direction**: overall feel for a tool people work in (think pro audio tool, but approachable for non-experts). Avoid generic 'AI dashboard' look. Light/dark: pick a default and say why.
2. **Design tokens**: color palette (hex, with roles: bg, surface, border, text, muted, accent, success/warn/error, waveform original vs processed), typography (font families from Google Fonts or system, sizes), spacing scale, radius, shadows, motion durations/easing.
3. **Layout** for desktop (1280–1920 px) and a usable narrow fallback: where upload, media inspector, presets, operation list, waveform/preview, progress, results/A-B, history live. One-screen workflow vs steps — decide.
4. **Key components and states**: drop zone (idle/drag-over/uploading/error), operation card (off/on/disabled-by-conflict/unavailable-on-this-machine/GPU vs CPU badge, with params), preset chips, stage stepper with progress + ETA + cancel, A/B toggle (Tab hotkey), cut-region display on waveform, download buttons, empty states, error messages (copy examples).
5. **Microcopy**: plain-language names and one-line descriptions for each operation in SPEC §2 (non-experts must understand 'dehum', 'declick', 'loudness').
6. **Accessibility & keyboard**: focus, shortcuts, reduced motion, contrast.
7. **Top 5 UI mistakes to avoid** for this kind of tool.
