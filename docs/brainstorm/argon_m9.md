### 1. Answers to the 6 Open Questions

* **1. M9a / M9b Split:** Keep the two-phase split, but move **audio-only A/B playback + 15–30 s Preview submit** (`<AudioComparePlayer>` using dual `<audio>` elements + `useTimelineMap(jobId)`) into **M9a**, leaving canvas waveform rendering (`<WaveformWell>`, `<CutHatchOverlay>`, `<PreviewRegionHandles>`, video sync) for **M9b**. Users validate audio quality by ear immediately in M9a without waiting for wavesurfer canvas plumbing.
* **2. State & SSE (`useJob(jobId)`):**
  * **Persistence & Tab Re-open:** Store `activeMediaId` and `activeJobId` in `sessionStorage` + hydrate from `GET /api/jobs?limit=20` on mount so jobs finishing while the tab was closed show their completed/interrupted state immediately.
  * **SSE (`GET /api/jobs/{id}/events`):** Native `EventSource` listening to `state` and `done`. On `done` (or `onerror` backoff), call `GET /api/jobs/{id}` because [jobs.py](file:///F:/sound_system/backend/api/jobs.py#L227-L235) SSE payloads omit `files`.
  * **Multi-tab & Stale Estimates:** Sync `settings` and `activeJobId` across tabs via `BroadcastChannel('ss-sync')`. In `useEstimate(mediaId, ops, device)`, pass an `AbortController` to debounce (250 ms) and discard out-of-order responses.
* **3. 15-Op Rack UX (`<OpsRack>`, `<PresetBar>`, `<OpCard>`):**
  * Keep all 15 ops in fixed backend `order` along the spine ([UI_DESIGN.md](file:///F:/sound_system/docs/UI_DESIGN.md#L99-L107))—do **not** hide ops behind "More steps" (breaks the signal-chain mental model).
  * Keep off-cards compact (`52px` single-line summary); auto-scroll the rack to the first enabled card when a preset chip is clicked, and add a "Turn all off" / `Custom` reset action.
* **4. RTL + Wavesurfer (`wavesurfer.js@^7.8`):**
  * Wrap `<WaveformWell>`, `<TimeRuler>`, and timecode readouts in `<div dir="ltr">`. Render hover cut pills (`Removed 0:03.2`) and screen-reader announcements in inner `<span dir="rtl">` portals.
  * Build `<SegmentedControl>` (`Original | Processed`, `Graphics card | Processor`) with `role="radiogroup"`: compute arrow-key direction from `document.documentElement.dir` (`ArrowLeft` advances in RTL, `ArrowRight` in LTR) with roving `tabIndex={0|-1}` and no CSS flex-reverse.
* **5. Testing Strategy (`vitest@^3.0`, `@testing-library/react@^16.2`, Python Playwright):**
  * **Unit:** `formatMessage` covering all 6 `Intl.PluralRules('ar')` categories (`0, 1, 2, 5, 15, 100`), key parity (`ar satisfies typeof en`), and `Timeline` playhead translation (`src_to_out` / `next_kept_src`).
  * **Playwright E2E (1440 / 800 / 390 px, EN + AR):** (1) Upload Arabic-named `.mp4` → select `podcast` preset → verify conflict lock when toggling `karaoke` → run preview → run full job → A/B switch inside a cut span jumps to next kept frame; (2) Keyboard-only (`Space`, `KeyA`, `KeyB`, `BracketLeft/Right` via `event.code`); (3) RTL geometry assertions (`OpsRack.getBoundingClientRect().left > PlayerPanel.left`, zero horizontal overflow).
* **6. Missing vs. [UI_DESIGN.md](file:///F:/sound_system/docs/UI_DESIGN.md) / [SPEC.md](file:///F:/sound_system/docs/SPEC.md) / Backend API:** See Section 2 below.

---

### 2. Wrong or Missing in Draft, Frontend Shell & Backend API

* **No processed peaks endpoint:** [UI_DESIGN.md](file:///F:/sound_system/docs/UI_DESIGN.md#L109) draws both original and amber processed waveforms from backend peaks, but [runner.py](file:///F:/sound_system/backend/pipeline/runner.py#L257) deletes `work/` without writing `output/peaks.json`, and [jobs.py](file:///F:/sound_system/backend/api/jobs.py) has no `GET /api/jobs/{id}/peaks`. Decoding a 60-min WAV in-browser via Web Audio OOMs the tab.
* **No HTTP Range on job outputs:** [jobs.py](file:///F:/sound_system/backend/api/jobs.py#L337-L344) serves `/api/jobs/{id}/files/{name}` as `application/octet-stream` without `206 Partial Content` (unlike `_file_response` in [media.py](file:///F:/sound_system/backend/api/media.py#L392-L423)), breaking `<video>`/`<audio>` seeking on processed files.
* **Missing `files` in SSE & History:** [jobs.py](file:///F:/sound_system/backend/api/jobs.py#L179-L196) `GET /api/jobs` and `GET /api/jobs/{id}/events` omit `files` (needed for History row size/re-download and Done dock buttons).
* **`eta_sec` is never written & bookend stages are English-only:** [store.py](file:///F:/sound_system/backend/jobs/store.py#L216-L236) never updates `eta_sec`, and [runner.py](file:///F:/sound_system/backend/pipeline/runner.py#L224-L231) emits English stage strings (`"Reading file"`, `spec.name`, `"Saving audio"`, `"Rebuilding video"`) instead of op IDs/codes ([SPEC.md](file:///F:/sound_system/docs/SPEC.md#L110)).
* **Existing RTL CSS bugs in M2 shell:** [OpsRack.tsx](file:///F:/sound_system/frontend/src/components/OpsRack.tsx#L36-L44) uses physical `left-[12px]` and `ml-auto` instead of `start-[12px]` / `ms-auto`; [index.css](file:///F:/sound_system/frontend/src/styles/index.css#L191-L194) `.switch` knob uses `translateX(16px)` which slides off-track in `dir="rtl"`.
* **Missing dependencies & TopBar controls:** [package.json](file:///F:/sound_system/frontend/package.json) lacks `@fontsource/ibm-plex-sans-arabic` and `wavesurfer.js`; draft omits the `?` shortcuts popover ([UI_DESIGN.md](file:///F:/sound_system/docs/UI_DESIGN.md#L150)), Settings gear popover, and polling `GET /api/media/{id}` until `ready.peaks` + `lufs` finish background prep.

---

### 3. Top 5 UX Risks for Non-Expert Arabic Users

1. **Silent `showSaveFilePicker` failure after `await`:** Calling any async fetch before `showSaveFilePicker` / `showDirectoryPicker` drops the transient user activation in Edge/Chrome, silently blocking the save dialog ([SPEC.md](file:///F:/sound_system/docs/SPEC.md#L117)).
2. **Broken BiDi in Windows paths & negative units:** Unisolated Arabic folder paths (`C:\Users\أحمد\...`) and signed technical values (`−16 LUFS`, `48 kHz`) scramble minus signs and slashes unless wrapped in `<bdi dir="ltr">` ([UI_DESIGN.md](file:///F:/sound_system/docs/UI_DESIGN.md#L239)).
3. **Confusing "Preparation" gap after upload:** `POST /api/media` returns `201` while `source.wav`, `peaks.json`, `playback.m4a`, and `lufs` are still computing in `_prepare` ([media.py](file:///F:/sound_system/backend/api/media.py#L256-L270)); without a clear `"جارٍ تجهيز الملف…"` state polling `GET /api/media/{id}`, the waveform and LUFS appear broken.
4. **Cut Silence surprises (`action: "remove"`):** Non-experts may fear "حذف الصمت" deletes quiet speech; the preview region CTA (`معاينة المقطع`) must be prominent before a 30-min video re-encode.
5. **Hidden native folder picker behind browser window:** If `POST /api/settings/output-dir/pick` opens behind Edge or times out, users think the app froze—show an active `"اختر المجلد من النافذة المفتوحة…"` state plus an immediate `"اكتب المسار بدلًا من ذلك"` fallback.
