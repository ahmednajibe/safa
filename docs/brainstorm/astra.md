I recommend **FastAPI + React/Vite, FFmpeg for media handling, DeepFilterNet 3 for speech denoising, and Demucs for music separation**. Run one processing job at a time, with Python 3.11 managed by uv. Build a dependable FFmpeg pipeline first, then add ML operations behind independently verified adapters.

The GTX 1650 is useful for separation, but CPU processing should remain a supported path. Avoid making heavyweight generative enhancement a requirement.

The following speeds are **planning estimates, not benchmarks on this machine**. RTF means processing time divided by input duration: 0.5 RTF means approximately five minutes for ten minutes of audio. Model loading, downloads, and video encoding are additional.

| Operation | Recommended implementation and rationale | Approximate speed |
|---|---|---|
| Speech noise reduction | **DeepFilterNet 3**: efficient speech denoising at 48 kHz. Good for fans, hiss, and background noise; aggressive settings can damage speech. | CPU roughly 0.1–0.5 RTF; GPU optional, with limited benefit for short clips. |
| General noise reduction | **FFmpeg `afftdn`**: dependable installation and adjustable FFT denoising. Useful fallback when the AI runtime is unavailable. | CPU roughly 0.02–0.2 RTF. |
| Remove music / retain vocals | **Demucs v4 `htdemucs`**, vocals output. Start with four-second segments, one shift, and overlap; reduce segment size if memory is insufficient. | GPU roughly 0.5–3 RTF; CPU roughly 3–15 RTF. |
| Karaoke / instrumental export | Demucs accompaniment output from the same separation pass. | Same separation cost; exporting another stem is cheap. |
| Improve speech clarity | **FFmpeg** high-pass, EQ, de-esser, and gentle compression. Predictable, inexpensive controls that improve intelligibility and consistency. | CPU roughly 0.01–0.1 RTF. |
| Normalize loudness | **FFmpeg two-pass `loudnorm`**. Offer −16 LUFS and −14 LUFS presets, with a −1 dBTP ceiling. | CPU roughly 0.05–0.3 RTF. |
| Shorten pauses / remove silence | **Silero VAD through ONNX Runtime CPU** for speech pauses; FFmpeg `silencedetect` for amplitude-based silence. | Analysis roughly 0.01–0.1 RTF; video rendering is additional. |
| Remove hum and clicks | FFmpeg notch filters for 50/60 Hz and harmonics; conservative `adeclick` for transient clicks. | Usually below 0.3 RTF on CPU. |
| Transcription and subtitles | **faster-whisper**, multilingual `base` or `small`; CPU INT8 baseline, optional GPU acceleration after compatibility checks. Export SRT/VTT. | GPU roughly 0.1–0.5 RTF; CPU roughly 0.3–2 RTF, model dependent. |
| Convert, trim, change speed, fix channels | FFmpeg for WAV/FLAC/MP3/AAC, manual trimming, tempo changes, mono/stereo conversion, and selecting a clean microphone channel. | Usually well below realtime for audio; video changes may require encoding. |

Demucs separates **vocals from accompaniment**: singing can remain alongside dialogue. Its two-stem option does not make the underlying model a lightweight two-source network.

Defer **resemble-enhance** and AI bandwidth extension. They introduce dependency, memory, and voice-alteration risks; acceptable speed on 4 GB is uncertain. Resampling to a higher sample rate does not restore lost detail. Later, evaluate a specific dereverberation model through `audio-separator`, with its own installation, quality, and memory checks.

The pipeline should be:

**Probe → select stream/manual trim → decode → separate music → denoise → corrective filters → detect/edit pauses → compression → loudness normalization → encode → remux.**

Separation comes before denoising because it expects the original mixture, and aggressive denoising can obscure separation cues. Detect pauses on cleaned speech before compression raises residual noise. Normalize after cuts and dynamics so the measurement reflects the actual export. Transcribe the final edited audio so subtitles match its timeline.

Operations need conditional rules: karaoke should disable speech denoising and speech-based pause removal by default. Allow one denoiser at a time. Present the effective order automatically rather than requiring users to arrange it.

Use a file-backed floating-point audio intermediate, with adapters handling model-specific rates: Demucs typically needs 44.1 kHz; DeepFilterNet needs 48 kHz. Preserve stereo unless an operation explicitly requires downmixing, and compensate for model latency or length changes. Bound memory through chunks and overlap; model segmentation alone does not guarantee that full-file buffers stay small.

For the backend, use **FastAPI, SQLite, and one supervised worker subprocess**. SQLite stores queued, running, completed, failed, cancelled, and interrupted jobs. The API remains responsive while the worker processes media; only one model occupies GPU memory at a time. Subprocess isolation also makes cancellation and memory recovery more dependable.

Use **SSE** for progress, backed by persisted job state so refreshing the page reconnects safely. Report the current operation, completed chunks, elapsed time, and estimated time when enough measurements exist. Show indeterminate progress when a library provides no meaningful percentage. Cancellation must stop the worker’s FFmpeg children too. On restart, mark unfinished work interrupted and offer retry.

Store uploads and artifacts under `data/jobs/<uuid>/`, retaining originals until explicit deletion or a visible retention deadline. Default to seven-day retention for completed jobs, exempt active jobs, and provide “Delete now.” Check disk space before starting: one hour of stereo 48 kHz float audio consumes approximately 1.4 GB per intermediate.

For video, probe streams with **ffprobe** and let users choose the audio track. Without timeline edits, preserve the video stream through stream copy and encode only processed audio. Preserve its original timing relationship to the video. Use MP4/AAC when compatible and offer MKV when it avoids unnecessary video transcoding.

For silence cuts, generate one shared list of retained intervals and apply it to audio and video. Arbitrary cuts normally require video re-encoding; offer H.264 NVENC when available, with x264 fallback. Keep 150–300 ms around speech and use short audio boundary fades. Do not silently retain subtitle tracks whose timestamps became invalid.

Bundle or install a pinned Windows FFmpeg distribution containing **both `ffmpeg` and `ffprobe`**. `imageio-ffmpeg` alone should not be the dependency strategy because ffprobe is also needed.

Use **uv-managed Python 3.11**, leaving the installed 3.13 untouched. Some current PyTorch releases support 3.13, but that does not establish compatibility across Demucs, torchaudio, DeepFilterNet’s native components, and older enhancement dependencies. Pin a verified torch/torchaudio pair and a CUDA wheel supporting the GTX 1650; the driver number alone is insufficient validation.

Treat native Windows DeepFilterNet installation as an early feasibility gate. If its wheels or dependencies fail, ship the FFmpeg fallback while resolving the isolated ML environment. Keep resemble-enhance in a separate optional environment if eventually adopted. Prefer **Node 24 LTS** for reproducible frontend builds; Node is unnecessary at runtime once FastAPI serves the built assets.

The UI should have a drop zone, media details, audio-track selection, operation toggles, and expandable settings. Offer **Podcast**, **Lecture**, **Voice over music**, and **Karaoke** presets. Add a selectable 20–30-second preview before a long run.

Use wavesurfer.js with backend-generated waveform peaks to avoid decoding large files entirely in browser memory. Provide synchronized A/B playback, an optional loudness-matched comparison, and highlighted proposed cuts. After timeline edits, synchronize comparisons through the retained-interval map. Display queue position, cancellation, actionable errors, and separate audio/video/subtitle downloads.

The project can stay compact:

- `backend/app/`: API, settings, database, job supervisor.
- `backend/app/media/`: probing, decoding, encoding, remuxing, timeline edits.
- `backend/app/operations/`: validated operation adapters and pipeline rules.
- `frontend/`: React, TypeScript, Vite, waveform and job screens.
- `models/`, `tools/`, `data/`: cached weights, FFmpeg binaries, job artifacts.
- `tests/fixtures/`, `scripts/`: small media samples, setup, diagnostics, launcher.

Implement it in these ordered milestones:

1. **Environment feasibility:** verify FFmpeg/ffprobe, CPU inference, CUDA, and short Demucs/DeepFilterNet samples. **Done:** document working pins, memory use, timings, and fallbacks.
2. **Local application shell:** launcher, health checks, built frontend. **Done:** one command opens the app on localhost.
3. **Media round trip:** upload, probe, stream selection, conversion, download. **Done:** audio and uncut video exports play with correct synchronization.
4. **Durable jobs:** worker, SQLite states, SSE, cancellation, recovery. **Done:** refresh preserves progress; cancellation stops processing.
5. **FFmpeg operations:** filters, dynamics, normalization, presets. **Done:** measured loudness and peak limits meet settings on representative fixtures.
6. **Pause editing:** cut preview and shared timeline. **Done:** quiet speech survives; edited video remains synchronized.
7. **AI denoising:** bounded-memory adapter and fallback. **Done:** long input completes within RAM limits.
8. **Music separation:** segmentation, retry, stem export. **Done:** a representative file completes on 4 GB and CPU fallback works.
9. **Transcription and comparison:** subtitles, waveform, mapped A/B. **Done:** captions and playback align after cuts.
10. **Offline packaging:** model manifest, cleanup, documentation. **Done:** all installed features work with networking disabled.

The main risks are memory exhaustion, damaged speech, timing drift, and installation fragility. Address them with bounded buffers, conservative defaults, previewable cuts, timestamp checks, and pinned dependencies. Cache all models and frontend assets locally, verify downloads and licenses, bind to loopback, validate request origins, and pass Windows paths as argument arrays. Include Arabic filenames and long media in acceptance checks.
