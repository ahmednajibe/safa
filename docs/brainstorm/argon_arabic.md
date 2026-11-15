### 1. Arabic Copy Revisions ([UI_DESIGN.md](file:///F:/sound_system/docs/UI_DESIGN.md#L172-L227) §12)

*Unify verb voice across ops (3rd-person present), replace literal calques (`الموسيقى الخلفية` → `موسيقى الخلفية`), avoid "ترجمة" for same-language transcription, and fix the `enhance` mistranslation:*

- `drop.title`: **اسحب ملف صوت أو فيديو وأفلته هنا** *(natural phrasing vs abrupt "أفلت")*
- `drop.release`: **أفلت الملف لإضافته**
- `pill.modelMissing`: **نموذج الذكاء الاصطناعي غير مُنزَّل — اتصل بالإنترنت مرة واحدة لتنزيله** *(matches `تنزيل`; bare "النموذج" means "form" to non-tech users)*
- `device.gpu` / `device.gpuReady`: **كارت الشاشة** / **كارت الشاشة جاهز** *(more natural in Egypt/Levant, or keep `كرت الشاشة` consistently)*
- `device.fallback`: **امتلأت ذاكرة كارت الشاشة؛ يجري إكمال هذه الخطوة على المعالج.**
- `ab.processed`: **المُعالَج** *(or keep **بعد المعالجة**, shorter in segmented pill)*
- `msg.silence_removed`: **حُذفت {seconds} ثانية من الصمت (في {count} من المواضع)** *(spell out `ثانية` after decimals; `في {count} من المواضع` is grammatically valid for all numbers 1–999+, or use `Intl.PluralRules('ar')` for `موضع واحد / موضعين / {count} مواضع / {count} موضعًا`)*
- **Presets**: Podcast cleanup = **تنقية البودكاست** · Remove background music = **إزالة موسيقى الخلفية** · Fix echoey room = **معالجة صدى الغرفة** · Video jump-cut = **قص وقفات الفيديو** *(not "قص فيديو سريع" which implies speed)*
- `trim`: **قص الأطراف** — **يحتفظ بالجزء المحدد بين وقتَي البداية والنهاية فقط.**
- `channel`: **ضبط القنوات** — **يُبقي الصوت ستيريو، أو يدمجه في قناة واحدة، أو يختار اليسار أو اليمين فقط.**
- `highpass`: **إزالة الضجيج المنخفض** — **يكتم الأصوات العميقة كخبطات المكتب وهدير التكييف.**
- `separate`: **فصل الكلام عن الموسيقى** — **يحتفظ بصوت المتحدث فقط، أو بالموسيقى فقط، أو يحفظ الاثنين معًا.** *("الصوت" means all audio; "الكلام" is unambiguous)*
- `denoise`: **تقليل ضوضاء الخلفية** — *(idafa construction)*
- `enhance`: **تحسين الصوت** — **يوازن نبرة الصوت، ويخفف حدة حرف السين، ويساوي بين الكلمات العالية والمنخفضة.** *(draft mistranslated "evens out loud and quiet words" as "ويقلل الرنين المنخفض")*
- `loudness`: **ضبط مستوى الصوت** — **يثبّت الصوت عند مستوى مريح للاستماع (بودكاست −16 / يوتيوب −14) ويمنع التشوّش.**
- `subtitles`: **تفريغ النص (Subtitles)** — **يحوّل الكلام المسموع إلى نص متزامن بملف (SRT/VTT).** *("ترجمة" implies foreign-language translation)*

---

### 2. RTL Rules — Gaps & Fixes ([SPEC.md](file:///F:/sound_system/docs/SPEC.md#L105-L112) §11)

- **Shell & spine mirroring**: Flip the 2-column grid (Ops rack on right, Player on left), place the rack’s 1px spine at `inset-inline-start: 12px`, and slide the History drawer from `inline-end` (left in RTL).
- **Icons**: Flip directional UI icons (`scale: -1 1` on chevrons, drawer back/close). **Never flip** transport icons (`⏵`, seek), checkmarks, the brand mark, or the 45° cut-region hatch.
- **Waveform & transport LTR islands**: Keep waveform, ruler, preview handles, and timecodes in `dir="ltr"`. Give hover badges/announcements inside the well ("حُذفت 0:03.2 ث") `dir="rtl"` so Arabic punctuation doesn't invert.
- **Segmented controls & focus**: DOM order naturally reverses RTL tab order. Move `[الأصلي | المُعالَج]` outside the `dir="ltr"` transport wrapper (or set `dir="rtl"` on it) and invert `ArrowLeft`/`ArrowRight` roving focus when `dir="rtl"`.
- **Progress bars**: Fill upload/job/stage progress bars **right-to-left** in RTL, while keeping waveform playhead progress **left-to-right**.
- **Numbers, units & filenames**: Keep `0–9` with `tabular-nums`. Wrap signed technical values (`-23.4 LUFS`, `-1 dBTP`, `48 kHz`) in `<bdi dir="ltr">` so `-` stays on the left. Wrap filenames in `<bdi dir="auto">` *before* the ` · ` separator so `.mp4` doesn't jump across RTL text.
- **Arabic keyboard layout hotkeys**: Physical keys `A`/`B`/`P`/`[`/`]` emit `ش`/`لا`/`ح`/`ج`/`د` in Arabic layout. Listen to **`event.code`** (`KeyA`, `KeyB`, `KeyP`, `BracketLeft`, `BracketRight`), not `event.key`.

---

### 3. Arabic Typography (`IBM Plex Sans Arabic` + `Inter`)

- **Fit**: Excellent pairing with `Inter` on `#000`/`#111`—clean Naskh geometry, open counters, and native `tabular-nums`.
- **Adjustments**:
  - Apply `-webkit-font-smoothing: antialiased` on `<html>` so 500/600 white Arabic strokes don't bloom on true black, and force `letter-spacing: 0` in `[lang="ar"]`.
  - Bump `--fs-small` from `12px` to **`13px`** in `[lang="ar"]` and strip non-essential tashkeel/shadda on small pills.
  - Scope `line-height: 1.6` to body/descriptions (`14px`); keep controls, `36px` pills, and `20px` titles at **`1.35–1.4`** so vertical centering doesn't shift.

---

### 4. Backend Localizable Messages (`message_code` + `message_params`)

- **Pluralization**: Support `Intl.PluralRules('ar')` on the frontend for any `{count}` parameter.
- **Missing structured codes**:
  - **Upload/Storage**: `err.disk_full` (`{needed_gb, free_gb}`), `err.corrupt_media`, `err.gpu_unavailable`.
  - **Lifecycle/Models**: `progress.model_download` (`{model_id, pct}` — localize `model_id` on frontend), `msg.job_queued`, `msg.job_interrupted`.
  - **Conflicts & Availability**: Return `unavailable_reason_code` (`hardware_unsupported` | `model_not_cached` | `coming_later`), `conflict_reason_code` (`{blocker_op_id}`), and `preset.partial_unavailable` (`{skipped_op_ids}`).
  - **Estimates ([SPEC.md](file:///F:/sound_system/docs/SPEC.md#L97) §9)**: Return `bucket_code` (`under_1m` | `about_2m` | `about_10m` | `over_30m`) instead of English strings.
