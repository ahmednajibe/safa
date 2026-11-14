# M4b — Timeline & Video Implementation Plan

> **For agentic workers:** Implement task-by-task, test-first. Do NOT commit. Read `AGENTS.md`, `docs/SPEC.md` (§3, §8 "Timeline model", §9), `backend/operations/base.py`, `backend/pipeline/runner.py`, `backend/media/encode.py` first. M1–M4a are done.

**Goal:** Ops that change length (`trim`, `speed`, and later `silence`) produce a composable timeline map; audio is cut sample-accurately with short non-overlapping fades; video is re-rendered from the same map so audio and video durations stay identical; the map is saved with the job for the UI (A/B playhead translation, cut list, subtitles).

**Architecture:** `backend/media/timeline.py` (pure math, no I/O), `backend/media/cutter.py` (streaming audio renderer for keep-intervals), `backend/media/video_render.py` (one ffmpeg pass using a filter script). Runner composes each op's `OpResult.timeline` and decides remux vs re-render at the end.

**Tech Stack:** numpy, soundfile, ffmpeg (`select`, `setpts`, `atempo`, `-filter_complex_script`), `h264_nvenc` / `libx264`.

**Spec:** `docs/SPEC.md`

## Global Constraints
- Times are seconds (float) in the API, but cuts are computed in **samples** (48 kHz) for audio and snapped to the **video frame grid** (1/fps) when the job has video, so both streams cut at the same instants.
- Never overlapping crossfades: each kept segment gets ≤10 ms fade-in at its start and fade-out at its end, inside the segment (SPEC §8). Output length = Σ segment lengths exactly.
- Filtergraphs for video are always written to `work/video_filter.txt` and passed with `-filter_complex_script` (Windows 32 KB command-line limit).
- Encoder: job device `gpu` and `has_encoder("h264_nvenc")` → `h264_nvenc -preset p5 -cq 20 -b:v 0`; otherwise `libx264 -preset medium -crf 20`. Job device `cpu` → **always** libx264 (SPEC §9 hard ceiling). Pixel format `yuv420p`, audio AAC 256k, `-movflags +faststart`. If nvenc fails at runtime → retry once with libx264 and record `fallback_reason`.
- Video is only re-encoded when the timeline is not identity; otherwise M3a's `-c:v copy` remux path stays.

## Review Focus
1. Trim + speed 1.5 on `clip.mp4` → output video and audio durations equal within one frame (1/25 s) and equal to the timeline's `out_duration` (test).
2. 400 keep-intervals (generated) on a 60 s video → render succeeds via filter script; no command-line length error (test).
3. Variable-frame-rate input (generate with ffmpeg `-vf "setpts=..."` or `-fps_mode vfr` from mixed-rate concat) → rendered output still in sync (constant-fps output at the source's average fps) (test).
4. Cut boundaries are click-free: for a 1 kHz sine cut in 10 places, the max sample-to-sample jump in the output is ≤ the max jump of the unmodified sine × 1.5 (test).
5. `out_to_src`/`src_to_out` round-trip for every kept point; a source time inside a removed span maps to `None`, and `next_kept_src(t)` returns the next kept boundary (test, property-style over random intervals with a fixed seed).

---

### Task 1: Timeline math
**Files:** Create `backend/media/timeline.py`; tests `tests/test_timeline.py`.

**Interfaces — Produces:**
- `@dataclass(frozen=True) Segment(src_start: float, src_end: float, out_start: float, speed: float = 1.0)` with `out_end` property = `out_start + (src_end - src_start) / speed`.
- `@dataclass(frozen=True) Timeline(source_duration: float, segments: tuple[Segment, ...])` with:
  - `identity(duration) -> Timeline` (classmethod); `is_identity -> bool`; `out_duration -> float`
  - `keep(intervals_out: list[tuple[float, float]]) -> Timeline` — intervals expressed in **this timeline's output time**; returns the composed timeline (used by trim and silence).
  - `with_speed(factor: float) -> Timeline` — composes a global speed change.
  - `src_to_out(t: float) -> float | None`; `out_to_src(t: float) -> float`; `next_kept_src(t: float) -> float | None`
  - `removed_spans() -> list[tuple[float, float]]` (source time); `kept_spans_src() -> list[tuple[float, float]]`
  - `snap(fps: float) -> Timeline` — snaps every segment boundary to `round(t*fps)/fps` in source time, dropping segments shorter than one frame.
  - `to_dict() / from_dict()` — JSON form `{"source_duration", "segments":[{"src_start","src_end","out_start","out_end","speed"}], "removed":[[a,b],...]}`.
- Tests: identity, trim (keep one interval), keep after trim (composition), speed after keep, Review Focus 5, snap.

### Task 2: Audio cutter + trim/speed ops
**Files:** Create `backend/media/cutter.py`, `backend/operations/impl/trim.py`, `backend/operations/impl/speed.py`; modify `backend/operations/base.py` (`OpContext.fps: float | None`, `OpContext.timeline: Timeline` = timeline so far); tests `tests/test_cutter.py`, `tests/test_ops_timeline.py`.

**Interfaces — Produces:**
- `cutter.py`: `render_keep(in_wav: Path, out_wav: Path, keep: list[tuple[float, float]], fade_ms: float = 10.0, report=None, should_cancel=None) -> int` (frames written) — streaming with `soundfile` (`seek` + block reads), boundaries in samples, raised-cosine fades inside each segment (fade length = min(fade_ms, segment/4)).
- `trim` op: `params start_s`, `end_s` (None = end) in the op's input time → `keep=[(start, end)]` → snapped to `ctx.fps` if video → `render_keep` → `OpResult(timeline=ctx.timeline.keep(...))`. Invalid (start ≥ end, beyond duration) → `ValueError` with a human message (surfaces as job failure "Trim start must be before the end").
- `speed` op: `atempo=<factor>` via `run_filter`; afterwards force exact length `round(n_in / factor)` (pad/trim ≤ a few ms, documented); `OpResult(timeline=ctx.timeline.with_speed(factor))`; factor 1.0 → passthrough.
- Runner: maintains `timeline` (starts identity at decoded duration; preview jobs start at the preview length), passes it in `OpContext`, replaces it with `OpResult.timeline` when set; sets `ctx.fps` from the probe's video stream (`None` for audio-only or `keep_video=False`).
- Tests: Review Focus 4, render_keep length exact, trim on real recording (skip if missing) duration exact, speed 1.5 / 0.75 length exact and pitch preserved (dominant frequency of a 440 Hz sine stays 440 ±2 Hz).

### Task 3: Video renderer
**Files:** Create `backend/media/video_render.py`; tests `tests/test_video_render.py`.

**Interfaces — Produces:** `render_video(video_src: Path, video_stream_index: int, audio_wav: Path, timeline: Timeline, dst_stem: Path, device: Literal["gpu","cpu"], fps: float, report, should_cancel) -> tuple[Path, str]` (path, encoder used). Filter script: `[0:v:<i>]fps=<fps>,select='<or of between(t,a,b)>',setpts=N/(<fps>*TB)` then, if any segment speed ≠ 1, `setpts=PTS/<speed>` (global speed only in this milestone) + matching `fps` to keep CFR; output `[v]`; `-map [v] -map 1:a:0`. Uses `run_ffmpeg` with progress and cancel. Container from `video_container_for` (mkv inputs with non-mp4-safe codecs re-encode to h264 in mp4 anyway since we re-encode — always output `.mp4`).
- Runner end stage "Rebuilding video": identity timeline → existing remux; otherwise `render_video`. Saves `output/timeline.json` (always, also identity) — not registered as a downloadable file; add `GET /api/jobs/{id}/timeline` returning it (404 until completed).
- Tests: Review Focus 1, 2, 3; cpu job uses libx264 (assert encoder string), gpu job uses nvenc when available (skip if not), nvenc failure fallback (monkeypatch to fail first call), cancel mid-render.

### Task 4: End-to-end
- Via the jobs API: `clip.mp4` with `trim (1, 8)` + `speed 1.25` + `loudness` → completed, output mp4 duration = timeline out_duration ±1 frame, `/timeline` returns removed spans; real recording (audio only) with trim → timeline.json present.
- [ ] `uv run pytest -v` → all pass.
- [ ] Leave `data/review/m4b_clip.mp4` for the orchestrator to watch.

## Final report
Files, commands + outcomes, measured durations, encoder used, deviations, open problems.
