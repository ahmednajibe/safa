Design review round for F:\sound_system (local audio/video cleanup web app: FastAPI on 127.0.0.1:8790 + React/Vite in the browser, Windows 11; the user and the server are on the same PC).

The user asked: I want the user to choose the path where the output is saved. Claude drafted a design: read docs/SPEC.md section 12 and docs/UI_DESIGN.md section 13 (and section 12 for Arabic copy). Current behaviour: download buttons are plain browser downloads into the Downloads folder; job outputs also stay in data/jobs/<id>/output for 7 days.

Load and use the skills that fit: brainstorming, design-taste-frontend (app-screen rules), web-design-guidelines, make-interfaces-feel-better.
You may read files; do NOT write or change files, no installs, never delete anything. Reply max ~450 words, bullets:
1. Is showSaveFilePicker (with a fallback) the right default? Pitfalls: large video files (streaming into the handle, progress, memory), user gesture requirement, Edge/Chrome support, cancelling, file type filters, suggested Arabic file names.
2. The 'Always save to' folder via a backend-opened native tkinter folder dialog: risks (dialog appearing behind the browser window, focus, DPI, blocking, running in a subprocess, server started without a desktop session), better alternatives, path validation/security.
3. Auto-copy behaviour: copy vs move, name collisions, what if the folder disappears (USB removed), partial writes, disk space, preview jobs (should they never auto-save?).
4. UI wording/placement (English + Arabic) and anything missing (e.g. remembering last folder per file type, Show in folder).
5. Concrete changes you recommend.
