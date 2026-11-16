# Sound System

A private, local app that cleans up your audio and video recordings —
cuts silence, removes noise, levels the volume, separates voice from
music, and writes subtitles. Everything runs on **your** computer; no
file ever leaves it.

---

## Quick start

Double-click **`start.bat`**.

The first run sets everything up by itself (Python, ffmpeg, the app,
and the AI models) — this can take 10–30 minutes depending on your
internet. After that, every start is just a few seconds and works
**fully offline**.

A browser tab opens automatically at `http://127.0.0.1:8790`. To stop
the app, close the console window (or press `Ctrl+C` inside it).

## First run: what gets downloaded

About **590 MB** of AI models are fetched once and kept in the app's
`models/` folder:

| Model                    | Size      | Used for                     |
| ------------------------ | --------- | ---------------------------- |
| DeepFilterNet3           | ~9 MB     | Reduce background noise      |
| HTDemucs                 | ~84 MB    | Separate voice / music       |
| faster-whisper small     | ~486 MB   | Subtitles (fast)             |
| Silero VAD               | bundled   | Cut silence                  |

Optional, larger models (**~1.7 GB** more) are available on demand —
either a **Download model** button appears right on the unavailable
step card, or you fetch everything once with:

```
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup.ps1 -AllModels
```

- `whisper_turbo` (~1.6 GB) — more accurate subtitles
- `Kim_Vocal_2` (~67 MB) — alternative separation model

## Share with a friend

Give them one ZIP — no Python, Node, or installation needed:

| ZIP                                  | Size      | Pick when                    |
| ------------------------------------ | --------- | ---------------------------- |
| `SoundSystem-*-win64-cpu.zip`        | ~0.5 GB   | any laptop (works everywhere)|
| `SoundSystem-*-win64-gpu.zip`        | ~4.6 GB   | they have an NVIDIA card     |

Steps for them:

1. Unzip to a **short path outside OneDrive** (e.g. `D:\SoundSystem`).
   Needs ~2 GB free for the CPU build, ~9 GB for the GPU build.
2. Double-click **`start.bat`**.
3. First run downloads **~600 MB** of AI models once — after that it
   works completely offline. Everything (models, jobs, logs) stays
   inside the app folder; nothing touches C:.

## Using the app

1. **Drop a file** into the window (audio or video).
2. **Pick the steps** you want — or use a preset like "Podcast cleanup".
   Steps run top to bottom.
3. Press **Process**. Watch the progress; cancel any time.
4. Results appear in the dock and in **History**. Save them wherever
   you like — or set "Always save to" in Settings and every result is
   copied there automatically.
5. Finished work is cleaned up automatically (default: after 7 days —
   change it in Settings, or tick **Keep forever** on a job).

The app speaks **English and Arabic** — it follows your browser/system
language and you can switch it in the header.

## Privacy

- Every file stays on your computer. Nothing is uploaded anywhere.
- The app only listens on `127.0.0.1` (your own machine) — other
  computers on your network cannot reach it.
- After the one-time model download, it works completely offline.
- Job files, logs, and model caches all live inside the app folder
  (`data/` and `models/`), not on your C: drive.

## Troubleshooting

**It says "port is in use by another program"**
Another app holds port 8790. Set a different port once, then run again:

```
set SOUND_SYSTEM_PORT=8800
start.bat
```

**"The graphics card isn't available" / GPU not detected**
That's fine — the app automatically uses the processor instead. It's
slower, but everything still works. If you have an NVIDIA card, updating
its driver can enable the faster path.

**"api-ms-win-crt / VC++ runtime" error when starting**
Windows is missing the Microsoft C++ runtime. Install it once from
Microsoft: https://aka.ms/vs/17/release/vc_redist.x64.exe then run
`start.bat` again.

**A step card says "Model not downloaded yet"**
Connect to the internet once and press the **Download model** button on
that card — or re-run `scripts\setup.ps1` to fetch all core models.

**Setup or a download fails halfway**
Just run `start.bat` (or `scripts\setup.ps1`) again — everything
already downloaded is kept and only the missing parts are retried.

**Low disk space / C: is almost full**
The app deliberately keeps all of its temporary files, caches, logs and
models **inside its own folder** — it never fills C:. Keep a few GB
free on the drive where the app lives.

**Warning about OneDrive or a very long path**
These are warnings, not errors — but moving the app folder out of
OneDrive (or to a shorter path like `D:\SoundSystem`) avoids file
locking/sync issues.

## Updating

Replace the app files with the new version (or `git pull`), then just
run `start.bat` again — it detects what changed, syncs Python packages
and rebuilds the app by itself. Your `data/` and `models/` folders are
kept.

## Uninstalling

Close the app, then delete the app folder. That's it — nothing was
installed into Windows, nothing else was touched.
