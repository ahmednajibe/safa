R1 full backend code review for F:\sound_system (local audio/video cleanup app, FastAPI on 127.0.0.1:8790, worker subprocess per job, SQLite, ffmpeg + ML engines on a GTX 1650 4 GB, Windows 11, Python 3.11). The backend is finished (milestones M1-M8). Read docs/SPEC.md and AGENTS.md for intent, then review ALL code in backend/ (api, core, jobs, media, operations, pipeline, worker.py), scripts/, and the tests in tests/.

Load and use the skills that fit: systematic-debugging, requesting-code-review / receiving-code-review, verification-before-completion, security review thinking.

You may read files and run read-only commands; do NOT write or change any file, do not run installs, never delete anything, do not start servers.

Report findings only (max ~900 words), most severe first. For each: file:line, severity (critical / high / medium / low), the concrete failure scenario (input/state -> wrong result/crash/security issue), and the fix in one line. Cover:
1. Correctness bugs (wrong results, races between API/supervisor/worker/SQLite, state machine holes, timeline/sample math, resource/VRAM/file-handle leaks, Windows path/encoding issues incl. Arabic names, subprocess/ffmpeg handling, cancellation).
2. Security: path traversal, arbitrary file read/write, command injection, CSRF/Origin, unsafe deserialization, DoS (huge uploads, disk fill), anything reachable from a browser tab on another site.
3. Error handling that hides failures or leaves junk on disk.
4. TEST QUALITY: tests that cannot fail (asserting tautologies, over-mocked, skipped silently, tolerance so wide nothing is checked), important behaviour with no test, flaky/slow tests. Name the weakest 10 tests and what they should assert instead.
5. Known open items to confirm or refute: intermittent Demucs LF rumble (suspect random 'shifts' or cuDNN nondeterminism in backend/media/separator.py), separate.requires_models gating on both models.
