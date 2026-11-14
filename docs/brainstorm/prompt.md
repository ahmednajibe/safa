# Brainstorm request: local audio/video cleanup web app

You are one of three senior engineers (GPT-6 Astra, Gemini 4 Argon, Claude Opus) designing a project together.
Do NOT write code or files. Reply with a written proposal only.

## What the user wants
A web application that runs **locally** (on their own PC, not a cloud service). The user:
1. Uploads an audio OR video file.
2. Chooses one or more processing operations, e.g.:
   - Clear / reduce background noise
   - Increase / enhance sound quality (speech enhancement, upscaling, EQ, loudness)
   - Remove music (keep the voice) — source separation
   - Cut silent parts
   - ...and **you should propose other useful operations** too.
3. The selected operations run as a pipeline (several at once, in a sensible order).
4. Downloads the result (audio; and for video input, ideally the video with the processed audio re-muxed).

## Target machine (hard constraints)
- Windows 11, Intel i5-10300H (4 cores / 8 threads), 16 GB RAM
- NVIDIA GTX 1650 with **4 GB VRAM** (CUDA capable, but small), driver 610
- Python 3.13 installed (uv available), Node 25 installed
- **ffmpeg is NOT installed yet** (we can install it or bundle it, e.g. via winget or imageio-ffmpeg / static build)
- Everything must work offline after first-time model download. No paid APIs.

## What I need from you (be concrete and opinionated)
1. **Feature list**: the core operations plus your extra suggestions. For each: what it does, the best open-source library/model for it on this hardware (name + why), and rough speed on a 4 GB GPU / CPU fallback.
2. **Pipeline order**: the correct order to apply operations when several are chosen, and why (e.g. separate music before denoising? trim silence last?).
3. **Architecture**: backend framework, frontend framework, job queue/progress reporting (long jobs!), file storage/cleanup, how video is handled (extract audio → process → remux).
4. **Python 3.13 compatibility risks**: which ML libs (torch, demucs, deepfilternet, resemble-enhance, etc.) may not support 3.13 and how to handle it (e.g. uv-managed Python 3.11/3.12 venv).
5. **UX**: what the UI should look like (upload, operation picker with options/presets, before/after A/B player, waveform, progress, download).
6. **Project structure** and an **ordered implementation plan** in milestones small enough to hand to a coding agent one at a time, each with a clear "done" check.
7. **Top risks** and how to avoid them.

Keep it under ~1500 words. Prefer things that actually run well on a GTX 1650 on Windows.
