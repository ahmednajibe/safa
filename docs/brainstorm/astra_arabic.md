I’m using the three requested skills to review the Arabic copy, RTL behavior, and typography. I’ll keep this read-only and use the specified docs as the source of truth.
The loaded design-taste skill is an older version without app-screen rules; I’ll read the app-screen version at your listed `.claude` path. Impeccable’s context loader won’t run under this read-only review, so I’ll use the existing project docs directly.
- **1. Arabic copy:** I would change these rows; for operations, “description” means keep the existing name. [UI_DESIGN.md:175-227](file:///F:/sound_system/docs/UI_DESIGN.md)

- `drop.title`: اسحب ملف صوت أو فيديو إلى هنا
- `pill.modelMissing`: اتصل بالإنترنت لتنزيل ملفات المعالجة أول مرة.
- `device.label`: المعالجة باستخدام
- `device.gpu`: بطاقة الشاشة
- `device.gpuReady`: بطاقة الشاشة جاهزة
- `device.fallback`: امتلأت ذاكرة بطاقة الشاشة. نكمل هذه الخطوة بالمعالج.
- `ab.original`: قبل المعالجة
- `progress.estimating`: جارٍ تقدير الوقت…
- `err.unsupported`: صيغة الملف غير مدعومة. اختر ملف صوت أو فيديو آخر.
- `msg.silence_removed`: الصمت المحذوف: {seconds} ث؛ عدد المواضع: {count}  
  This avoids incorrect number agreement, including decimals. Full sentences require Arabic plural branches—not a universal «موضعًا».
- `channel`: **ضبط قنوات الصوت** — احتفظ بالجانبين، أو ادمجهما في قناة واحدة، أو اختر الأيسر أو الأيمن.
- `highpass`: **تقليل الدوي** — يخفف دوي التكييف وصوت ارتطام المكتب.
- `dereverb` description: يخفف صدى الصوت في الغرف الكبيرة أو الفارغة.
- `separate` description: احتفظ بالصوت البشري أو الموسيقى، أو نزّل كلًا منهما منفصلًا.
- `declick` description: يقلل طقطقة الفم والشفاه مع الحفاظ على وضوح الكلام.
- `enhance` description: يوازن نبرة الصوت، ويخفف حدة السين، ويوحّد مستوى الكلمات العالية والخافتة.  
  The draft incorrectly substitutes rumble reduction for volume balancing.
- `silence`: **إزالة الصمت** — يزيل الصمت أو يقصّر فترات التوقف بين الكلام.
- `subtitles`: **إنشاء نص مصاحب** — يحوّل الكلام إلى نص متزامن مع الصوت النهائي (SRT/VTT).  
  «ترجمة» suggests language translation.
- Presets: `Podcast cleanup` → تنقية البودكاست; `Fix echoey room` → تقليل صدى الغرفة; `Karaoke` → موسيقى دون غناء; `Video jump-cut` → حذف الصمت من الفيديو.

- **2. RTL:** The foundation is sound, but specify these exceptions. [SPEC.md:105-111](file:///F:/sound_system/docs/SPEC.md)
  Mirror navigation arrows, rack spine and drawer placement; preserve playback icons, waveform, ruler, seeking and timecodes. Give Arabic labels and A/B/device segmented groups their own RTL direction—even inside transport. Place the first option rightmost; arrow navigation follows visual positions. Preserve logical DOM/Tab order; avoid double reversal. Processing/upload bars fill right-to-left; playback progress stays left-to-right. Use `ar-u-nu-latn`, isolated LTR number/unit/timecode groups, and whole-filename `<bdi dir="auto">`; keep sentence punctuation outside isolates. Use `event.code` (`KeyA`/`KeyB`) with layout-aware hints; scope shortcuts to player focus, respect focused controls, and ignore typing/composition. Switching language must preserve focus, playback and settings.

- **3. Typography:** IBM Plex Sans Arabic suits this restrained dark UI alongside Inter. Start at 15–16px body/controls, 13–14px helper text, 20–22px titles; body leading 1.6. Use 400 body, 500 controls, 600 headings; no Arabic tracking. Allow wrapping and 40px minimum control height. Explicitly retain Inter/tabular digits for technical readouts; verify diacritics and mixed baselines on Windows.

- **4. Backend:** Good contract direction; current job/result models still use strings. Define separate envelopes for status, errors and fallback warnings, with typed parameters, stable IDs and raw numbers. Cover validation/422 fields, conflicts, availability, model downloads, disk space, interruption/cancellation, expired outputs, and decode/encode/video stages. Persist codes/params through SQLite and SSE. Specify Arabic unknown-code fallback, legacy records, and six-category plural handling with decimal cases.

- Read-only review using file reads/searches and guideline retrieval. No files changed or installs; no tests or browser verification run.
