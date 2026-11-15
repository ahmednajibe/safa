"""Generate review files in ``data/review/`` (explicit, not pytest).

M6 + M6b (default): for every real recording, run the **Podcast preset**
through the real pipeline and write
  - ``m6_<stem>_podcast.wav``               (podcast chain minus breaths —
                                             the M6-era output)
  - ``m6_<stem>_podcast_keep_silence.wav``  (same preset minus silence
                                             and breaths)
  - ``m6b_<stem>_podcast.wav``              (full podcast chain incl.
                                             the M6b breaths op)
  - ``m6b_<stem>_breaths_only.wav``         (input − breaths-op output:
                                             exactly what was turned down)

and print the M6 report (durations, non-speech floor drop, SRMR, LTAS
ripple, resonance cuts, LUFS, true peak, stage timings) plus the M6b
report per recording: detected breath timestamps, measured dB drop over
the detected spans, and the faster-whisper transcript diff input vs
breaths-op output.

``--m7`` adds the separation files: each real recording is mixed with
the synthetic music bed at −10 dB SMR and run through
``separator.process_file`` on the job device —
  - ``m7_<stem>_with_music.wav``   (the mix fed to the engine)
  - ``m7_<stem>_voice.wav``        (voice stem)
  - ``m7_<stem>_music.wav``        (music stem)
plus SI-SDR of each stem against its ground truth (the recording is the
voice reference; the bed is the music reference) and seconds/device.

``--m5`` also regenerates the M5 files:
  - ``m5_<recording stem>_cut.wav`` per real recording (silence defaults)
  - ``m5_jumpcut.mp4`` (testsrc video + speech_silences.wav, keep_video)

``--m8`` runs ONLY the subtitle review (exclusive — unlike --m5/--m7 it
does not also run the M6 default chain): each real recording through
``run_pipeline([subtitles])`` on the job device with both models —
  - ``m8_<stem>_fast.srt``      (faster-whisper small)
  - ``m8_<stem>_accurate.srt``  (faster-whisper large-v3-turbo)
and reports per recording/language/cues/seconds/device plus a dedicated
turbo VRAM measurement (VramSampler around one accurate run on GPU).

Run:  uv run python scripts/make_review_files.py [--m5] [--m7] [--m8]
"""
from __future__ import annotations

import difflib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.core.capabilities import detect_capabilities  # noqa: E402
from backend.core.ffmpeg import find_ffmpeg  # noqa: E402
from backend.media.decode import decode_to_wav  # noqa: E402
from backend.media.loudness import integrated_lufs, true_peak_db  # noqa: E402
from backend.media.probe import probe  # noqa: E402
from backend.media.silence_detect import non_speech_dbfs  # noqa: E402
from backend.media.timeline import Timeline  # noqa: E402
from backend.operations.registry import OPERATIONS, PRESETS  # noqa: E402
from backend.operations.rules import resolve, unavailable_code  # noqa: E402
from backend.pipeline.runner import run_pipeline  # noqa: E402
from backend.pipeline.types import OutputSpec  # noqa: E402
from tests.audio_metrics import (  # noqa: E402
    ltas_ripple_db,
    load_mono,
    srmr_proxy,
)

REAL = ROOT / "tests" / "fixtures" / "real"
SPEECH_SIL = ROOT / "tests" / "fixtures" / "generated" / "speech_silences.wav"
SPEECH = ROOT / "tests" / "fixtures" / "generated" / "speech.wav"
REVIEW = ROOT / "data" / "review"
FPS = 25.0


# ---- M6: Podcast preset review files ------------------------------------------


def _job_device() -> str:
    """Same default the app uses: gpu when usable, else cpu."""
    caps = detect_capabilities()
    return (
        "gpu"
        if caps.gpu and caps.cuda_dlls and caps.modules.get("torch")
        else "cpu"
    )


def _preset_ops(preset: str, skip: set[str] | None = None):
    """Resolve a preset's ops through the real rule path (defaults filled,
    unavailable ops honestly dropped)."""
    caps = detect_capabilities()
    avail = {
        op_id: unavailable_code(spec, caps.modules, caps.models) is None
        for op_id, spec in OPERATIONS.items()
    }
    selection = {
        op_id: params
        for op_id, params in PRESETS[preset]["ops"].items()
        if op_id not in (skip or set())
    }
    eff = resolve(selection, avail)
    if eff.disabled:
        print(
            f"  NOTE: dropped unavailable ops: "
            f"{sorted(eff.disabled)}"
        )
    return eff.ops


def _run_ops(src: Path, tmp: Path, ops, device: str, tag: str) -> dict:
    """Run ``ops`` on ``src`` in a fresh job dir; return output path plus
    the closing per-stage Progress records (seconds/device/messages)."""
    p = probe(src)
    job_root = tmp / f"{src.stem.replace(' ', '_')}_{tag}"
    stages: dict = {}

    def on_progress(prog):
        if prog.stage_seconds is not None:
            stages[prog.stage] = prog

    outs = run_pipeline(
        media_path=src,
        probe=p,
        audio_stream_index=p.audio[0].index,
        ops=ops,
        output=OutputSpec(audio_format="wav", keep_video=False),
        job_root=job_root,
        on_progress=on_progress,
        should_cancel=lambda: False,
        device=device,
        display_name=src.stem,
    )
    out = next(o for o in outs if o.kind == "audio")
    return {"out": out.path, "stages": stages, "probe": p}


def _m6_one(rec: Path, tmp: Path, device: str) -> dict:
    """Podcast preset (M6-era: minus ``breaths``) on one recording."""
    ops = _preset_ops("podcast", skip={"breaths"})
    ops_keep = _preset_ops("podcast", skip={"silence", "breaths"})

    dst = REVIEW / f"m6_{rec.stem}_podcast.wav"
    dst_keep = REVIEW / f"m6_{rec.stem}_podcast_keep_silence.wav"

    run = _run_ops(rec, tmp, ops, device, "podcast")
    shutil.copy2(run["out"], dst)
    run_keep = _run_ops(rec, tmp, ops_keep, device, "keep")
    shutil.copy2(run_keep["out"], dst_keep)

    # non-speech floor drop, measured the honest way: input loudnorm'd to
    # the same -16 LUFS the pipeline applies, *same* VAD intervals on both
    # files, on the keep-silence output whose timeline matches the input.
    # (Silence removal swaps which non-speech segments remain and the
    # loudness stage shifts absolute dBFS — both would fake the number.)
    import subprocess as _sp

    from backend.media.silence_detect import speech_intervals_vad

    in_wav = tmp / f"{rec.stem.replace(' ', '_')}_in.wav"
    p = run["probe"]
    decode_to_wav(rec, in_wav, p.audio[0].index)
    normed = tmp / f"{rec.stem.replace(' ', '_')}_norm.wav"
    _sp.run(
        [
            str(find_ffmpeg()), "-v", "error", "-y", "-i", str(in_wav),
            "-af", "loudnorm=I=-16:TP=-1:LRA=11",
            "-ar", "48000", "-c:a", "pcm_f32le", str(normed),
        ],
        check=True,
    )
    intervals = speech_intervals_vad(normed)
    ns_in = non_speech_dbfs(normed, intervals)
    ns_out = non_speech_dbfs(dst_keep, intervals)
    x, _ = load_mono(in_wav)
    y, _ = load_mono(dst)
    dereverb = run["stages"].get("Reduce room echo")
    cuts = (
        (dereverb.message_params or {}).get("freqs")
        if dereverb else None
    )
    stage_rows = [
        (
            name,
            prog.stage_seconds,
            prog.device,
            prog.fallback_reason,
            prog.message_code,
            prog.message_params,
        )
        for name, prog in run["stages"].items()
    ]
    return {
        "stem": rec.stem,
        "path": dst,
        "path_keep": dst_keep,
        "in_s": p.duration_s,
        "out_s": probe(dst).duration_s,
        "keep_s": probe(dst_keep).duration_s,
        "ns_drop_db": (ns_in - ns_out)
        if ns_in == ns_in and ns_out == ns_out  # nan guard
        else float("nan"),
        "srmr_in": srmr_proxy(x, 48000),
        "srmr_out": srmr_proxy(y, 48000),
        "ripple_in": ltas_ripple_db(x, 48000),
        "ripple_out": ltas_ripple_db(y, 48000),
        "cuts": cuts,
        "lufs": integrated_lufs(dst),
        "true_peak": true_peak_db(dst),
        "device": device,
        "stages": stage_rows,
    }


def _m6_report(rows: list[dict]) -> None:
    for r in rows:
        print(f"\n=== {r['stem']} ===")
        print(f"  files:   {r['path'].name}")
        print(f"           {r['path_keep'].name}")
        print(
            f"  dur:     {r['in_s']:.2f}s -> {r['out_s']:.2f}s"
            f" (keep-silence variant {r['keep_s']:.2f}s)"
        )
        print(
            f"  non-speech floor drop (matched loudness,"
            f" same segments): {r['ns_drop_db']:+.1f} dB"
        )
        print(
            f"  SRMR proxy:  {r['srmr_in']:.2f} -> {r['srmr_out']:.2f} dB"
        )
        print(
            f"  LTAS ripple: {r['ripple_in']:.2f} -> {r['ripple_out']:.2f}"
            f" dB"
        )
        print(f"  resonance cuts: {r['cuts']}")
        print(
            f"  LUFS {r['lufs']}, true peak {r['true_peak']:+.1f} dBFS,"
            f" job device {r['device']}"
        )
        print("  stages:")
        for name, secs, dev, fallback, code, params in r["stages"]:
            fb = f"  (fallback: {fallback})" if fallback else ""
            detail = f" {code} {params}" if code else ""
            print(
                f"    {name:<28} {secs:6.1f}s  {dev}{fb}{detail}"
            )


# ---- M6b: breaths review files ----------------------------------------------------


def _whisper_model():
    """faster-whisper small on CPU, or None when unavailable."""
    try:
        from faster_whisper import WhisperModel

        from backend.core.models_manifest import model_dir

        return WhisperModel(
            "small", device="cpu", compute_type="int8",
            download_root=str(model_dir("whisper_small")),
        )
    except Exception as exc:  # module or weights missing — report, don't fail
        print(f"  NOTE: whisper unavailable ({exc}) — transcript diff skipped")
        return None


def _transcript(model, wav: Path) -> str | None:
    """Deterministic decode: temperature=0.0 disables the sampling
    fallback (identical bytes otherwise score 5–87 words across runs)
    and condition_on_previous_text=False stops error cascades."""
    if model is None:
        return None
    segs, _ = model.transcribe(
        str(wav), beam_size=5, temperature=0.0,
        condition_on_previous_text=False,
    )
    return " ".join(s.text.strip() for s in segs).strip()


def _word_diff(a: str | None, b: str | None):
    """Case/punctuation-insensitive word opcodes between transcripts."""
    if a is None or b is None:
        return None
    wa = re.findall(r"\w+", a.lower())
    wb = re.findall(r"\w+", b.lower())
    sm = difflib.SequenceMatcher(None, wa, wb, autojunk=False)
    return [
        (op, " ".join(wa[i1:i2]), " ".join(wb[j1:j2]))
        for op, i1, i2, j1, j2 in sm.get_opcodes()
        if op != "equal"
    ]


def _m6b_one(rec: Path, tmp: Path, device: str, model) -> dict:
    """Podcast chain + breaths residual + breath report for one file."""
    import soundfile as sf

    from backend.media.breaths import FRAME_S, detect_breaths

    stem = rec.stem.replace(" ", "_")
    p = probe(rec)
    in_wav = tmp / f"{stem}_m6b_in.wav"
    decode_to_wav(rec, in_wav, p.audio[0].index)

    # 1) breaths op alone → residual review file
    run_b = _run_ops(rec, tmp, [("breaths", {})], device, "breaths")
    xi, sr_i = sf.read(str(in_wav), dtype="float32", always_2d=True)
    xo, sr_o = sf.read(str(run_b["out"]), dtype="float32", always_2d=True)
    n = min(len(xi), len(xo))
    dst_res = REVIEW / f"m6b_{rec.stem}_breaths_only.wav"
    sf.write(
        str(dst_res), (xi[:n] - xo[:n]), sr_i, subtype="FLOAT"
    )

    # 2) full podcast chain (breaths included since M6b)
    run_p = _run_ops(rec, tmp, _preset_ops("podcast"), device, "m6b")
    dst = REVIEW / f"m6b_{rec.stem}_podcast.wav"
    shutil.copy2(run_p["out"], dst)

    # 3) report numbers: detections, per-span dB drop, transcript diff
    mono_in = xi.mean(axis=1)
    mono_out = xo.mean(axis=1)
    spans = detect_breaths(mono_in, sr_i)
    n_fr = int(FRAME_S * sr_i)
    k = n // n_fr
    e_in = 20 * np.log10(
        np.sqrt((mono_in[: k * n_fr].reshape(k, n_fr) ** 2).mean(1))
        + 1e-12
    )
    e_out = 20 * np.log10(
        np.sqrt((mono_out[: k * n_fr].reshape(k, n_fr) ** 2).mean(1))
        + 1e-12
    )
    span_rows = []
    for a, b in spans:
        i0, i1 = int(a / FRAME_S), min(int(b / FRAME_S) + 1, k)
        drop = float(np.median(e_in[i0:i1]) - np.median(e_out[i0:i1]))
        span_rows.append((a, b, drop))
    t_in = _transcript(model, in_wav)
    t_out = _transcript(model, run_b["out"])
    breaths_msg = next(
        (s for s in run_b["stages"].values() if s.message_code
         == "breaths_reduced"),
        None,
    )
    return {
        "stem": rec.stem,
        "path": dst,
        "path_res": dst_res,
        "spans": span_rows,
        "op_count": (
            breaths_msg.message_params.get("count") if breaths_msg else 0
        ),
        "op_db": (
            breaths_msg.message_params.get("db") if breaths_msg else None
        ),
        "diff": _word_diff(t_in, t_out),
        "t_in": t_in,
        "t_out": t_out,
        "device": device,
    }


def _m6b_report(rows: list[dict]) -> None:
    for r in rows:
        print(f"\n=== {r['stem']} (M6b) ===")
        print(f"  files:   {r['path'].name}")
        print(f"           {r['path_res'].name}")
        if not r["spans"]:
            print("  breaths: none detected (detector limit — see report)")
        else:
            print(f"  breaths detected ({len(r['spans'])}):")
            for a, b, drop in r["spans"]:
                print(
                    f"    {a:7.2f}–{b:7.2f} s   measured drop {drop:+.1f} dB"
                )
        print(
            f"  op message: count={r['op_count']} db={r['op_db']}"
        )
        if r["diff"] is None:
            print("  transcript: skipped (whisper unavailable)")
        elif not r["diff"]:
            print("  transcript: unchanged")
        else:
            print(f"  transcript diff ({len(r['diff'])} hunks):")
            for op, wa, wb in r["diff"]:
                print(f"    {op}: {wa!r} -> {wb!r}")
            print(f"    in : {r['t_in']}")
            print(f"    out: {r['t_out']}")


# ---- M7: separation review files -----------------------------------------------


def _m7_one(rec: Path, tmp: Path, device: str) -> dict:
    """Mix one recording with the fixture music bed at −10 dB SMR and
    separate it on ``device``. Ground truth exists for BOTH stems here:
    the recording is the voice reference, the bed the music reference."""
    import time as _time

    import soundfile as sf

    from backend.media import separator

    sys.path.insert(0, str(ROOT / "scripts"))
    from make_fixtures import _synth_sep_music

    from tests.audio_metrics import si_sdr_db_multi

    stem = rec.stem.replace(" ", "_")
    p = probe(rec)
    in_wav = tmp / f"{stem}_m7_in.wav"
    decode_to_wav(rec, in_wav, p.audio[0].index)
    x, sr = sf.read(str(in_wav), dtype="float32", always_2d=True)

    bed = _synth_sep_music(len(x))
    x_rms = float(np.sqrt((x ** 2).mean()))
    g = x_rms * (10.0 ** (-10.0 / 20.0)) / (
        float(np.sqrt((bed ** 2).mean())) + 1e-9
    )
    music_ref = (bed * g).astype(np.float32)
    mix = x + music_ref
    peak = float(np.abs(mix).max())
    k = 0.95 / peak if peak > 0.95 else 1.0
    mix = (mix * k).astype(np.float32)
    music_ref = (music_ref * k).astype(np.float32)
    voice_ref = (x * k).astype(np.float32)

    dst_mix = REVIEW / f"m7_{rec.stem}_with_music.wav"
    sf.write(str(dst_mix), mix, sr, subtype="FLOAT")
    mix_path = tmp / f"{stem}_m7_mix.wav"
    sf.write(str(mix_path), mix, sr, subtype="FLOAT")

    v_tmp, m_tmp = tmp / f"{stem}_v.wav", tmp / f"{stem}_m.wav"
    t0 = _time.monotonic()
    used = separator.process_file(
        mix_path, v_tmp, m_tmp,
        model="htdemucs", device=device, work_dir=tmp / f"{stem}_w",
    )
    seconds = _time.monotonic() - t0
    dst_v = REVIEW / f"m7_{rec.stem}_voice.wav"
    dst_m = REVIEW / f"m7_{rec.stem}_music.wav"
    shutil.copy2(v_tmp, dst_v)
    shutil.copy2(m_tmp, dst_m)

    v, _ = sf.read(str(dst_v), dtype="float32", always_2d=True)
    m, _ = sf.read(str(dst_m), dtype="float32", always_2d=True)
    return {
        "stem": rec.stem,
        "dur_s": p.duration_s,
        "seconds": seconds,
        "device": used,
        "voice_si_sdr": si_sdr_db_multi(v, voice_ref),
        "music_si_sdr": si_sdr_db_multi(m, music_ref),
        "mix_voice_si_sdr": si_sdr_db_multi(mix, voice_ref),
        "mix_music_si_sdr": si_sdr_db_multi(mix, music_ref),
    }


def _m7_report(rows: list[dict]) -> None:
    print(f"\n{'recording':<28} {'dur':>6} {'sep s':>6} {'dev':>4}"
          f" {'voice':>7} {'Δv':>6} {'music':>7} {'Δm':>6}")
    for r in rows:
        dv = r["voice_si_sdr"] - r["mix_voice_si_sdr"]
        dm = r["music_si_sdr"] - r["mix_music_si_sdr"]
        print(
            f"{r['stem']:<28} {r['dur_s']:>6.1f} {r['seconds']:>6.1f}"
            f" {r['device']:>4} {r['voice_si_sdr']:>6.1f}dB {dv:>+5.1f}dB"
            f" {r['music_si_sdr']:>6.1f}dB {dm:>+5.1f}dB"
        )


# ---- M8: subtitle review files --------------------------------------------------


def _m8_run(rec: Path, tmp: Path, device: str, model_key: str) -> dict:
    """One recording through the real pipeline with ``subtitles{model}``;
    copies the SRT to ``data/review/m8_<stem>_<model>.srt`` and returns
    language/cues/seconds/device/fallback pulled from the progress rows."""
    p = probe(rec)
    job_root = tmp / f"{rec.stem.replace(' ', '_')}_m8_{model_key}"
    stage: dict = {}
    msgs: list = []

    def on_progress(prog):
        if prog.stage_seconds is not None:
            stage["row"] = prog
        if prog.message_code:
            msgs.append(prog)

    outs = run_pipeline(
        media_path=rec,
        probe=p,
        audio_stream_index=p.audio[0].index,
        ops=[("subtitles", {"model": model_key})],
        output=OutputSpec(audio_format="wav", keep_video=False),
        job_root=job_root,
        on_progress=on_progress,
        should_cancel=lambda: False,
        device=device,
        display_name=rec.stem,
    )
    srt = next(o for o in outs if o.kind == "subtitle")
    dst = REVIEW / f"m8_{rec.stem}_{model_key}.srt"
    shutil.copy2(srt.path, dst)

    lang = next(
        (m.message_params for m in msgs
         if m.message_code == "language_detected"), {}
    )
    written = next(
        (m.message_params for m in reversed(msgs)
         if m.message_code == "subtitles_written"), {}
    )
    row = stage.get("row")
    return {
        "path": dst,
        "language": written.get("language") or lang.get("language") or "?",
        "lang_prob": lang.get("prob"),
        "cues": written.get("count"),
        "seconds": row.stage_seconds if row else None,
        "device": row.device if row else device,
        "fallback": row.fallback_reason if row else None,
    }


def _m8_turbo_vram() -> dict:
    """Turbo VRAM on this GPU: VramSampler around model load + one
    accurate transcription of the ~5 s speech fixture (M8 constraint:
    measured, not estimated)."""
    from backend.core.gpu import VramSampler
    from backend.media import transcribe

    import ctranslate2

    compute = (
        "int8_float32"
        if "int8_float32"
        in ctranslate2.get_supported_compute_types("cuda")
        else "float32"
    )
    with VramSampler() as vs:
        res = transcribe.transcribe_file(
            SPEECH, model="accurate", device="gpu"
        )
    return {
        "peak_mb": vs.peak_mb,
        "compute_type": compute,
        "device": res.device,
        "oom_fallback": res.oom_fallback,
        "cues_src": SPEECH.name,
    }


def _m8(recordings: list[Path], device: str) -> list[dict]:
    rows = []
    vram = None
    if device == "gpu":
        try:
            print("measuring large-v3-turbo VRAM on GPU…")
            vram = _m8_turbo_vram()
        except Exception as exc:  # report, don't kill the review run
            vram = {"error": str(exc)}
            print(f"  NOTE: turbo VRAM measurement failed: {exc}")
    with tempfile.TemporaryDirectory(prefix="m8_review_") as td:
        tmp = Path(td)
        for rec in recordings:
            print(f"processing {rec.name} (subtitles fast + accurate)…")
            rows.append(
                {
                    "stem": rec.stem,
                    "dur_s": probe(rec).duration_s,
                    "runs": {
                        k: _m8_run(rec, tmp, device, k)
                        for k in ("fast", "accurate")
                    },
                }
            )
    print()
    if vram:
        if "error" in vram:
            print(f"turbo VRAM: measurement failed: {vram['error']}")
        else:
            print(
                f"turbo VRAM: peak +{vram['peak_mb']} MB on GPU"
                f" (compute {vram['compute_type']},"
                f" device {vram['device']},"
                f" oom_fallback={vram['oom_fallback']})"
            )
    print(
        f"{'recording':<28} {'model':<9} {'lang':>10} {'cues':>5}"
        f" {'secs':>6} {'dev':>4}  file"
    )
    for r in rows:
        for k, run in r["runs"].items():
            lang = run["language"]
            if run["lang_prob"] is not None:
                lang = f"{lang}({run['lang_prob']:.2f})"
            secs = (
                f"{run['seconds']:6.1f}" if run["seconds"] is not None
                else "     ?"
            )
            fb = f"  ({run['fallback']})" if run["fallback"] else ""
            print(
                f"{r['stem']:<28} {k:<9} {lang:>10}"
                f" {run['cues']!s:>5} {secs}"
                f" {run['device']:>4}  {run['path'].name}{fb}"
            )
    return rows


# ---- M5: silence review files (unchanged, behind --m5) --------------------------


def _run_one(src: Path, dst: Path, tmp: Path, keep_video: bool) -> dict:
    """Run [silence defaults] on ``src``; copy the output to ``dst``."""
    p = probe(src)
    job_root = tmp / src.stem.replace(" ", "_")
    outs = run_pipeline(
        media_path=src,
        probe=p,
        audio_stream_index=p.audio[0].index,
        ops=[("silence", {})],
        output=OutputSpec(audio_format="wav", keep_video=keep_video),
        job_root=job_root,
        on_progress=lambda prog: None,
        should_cancel=lambda: False,
        device="cpu",
        display_name=dst.stem,
    )
    tl = Timeline.from_dict(
        json.loads((job_root / "output" / "timeline.json").read_text())
    )
    out = next(
        o for o in outs if o.kind == ("video" if keep_video else "audio")
    )
    shutil.copy2(out.path, dst)
    return {
        "in_s": p.duration_s,
        "out_s": probe(dst).duration_s,
        "removed_s": p.duration_s - probe(dst).duration_s,
        "cuts": len(tl.removed_spans()),
        "path": dst,
    }


def _make_jumpcut_src(tmp: Path) -> Path:
    """testsrc video + speech_silences.wav — same build as the e2e test
    (video runs a second longer than the audio)."""
    src = tmp / "jump_src.mp4"
    import soundfile as sf

    dur = sf.info(str(SPEECH_SIL)).frames / 48000
    subprocess.run(
        [
            str(find_ffmpeg()), "-hide_banner", "-y",
            "-f", "lavfi",
            "-i",
            f"testsrc=size=640x360:rate={FPS:g}:duration={dur + 1.0:.3f}",
            "-i", str(SPEECH_SIL),
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", str(src),
        ],
        check=True, capture_output=True, timeout=120,
    )
    return src


def _m5(recordings: list[Path]) -> list[dict]:
    rows = []
    with tempfile.TemporaryDirectory(prefix="m5_review_") as td:
        tmp = Path(td)
        for rec in recordings:
            dst = REVIEW / f"m5_{rec.stem}_cut.wav"
            rows.append(_run_one(rec, dst, tmp, keep_video=False))
        jc = _make_jumpcut_src(tmp)
        rows.append(
            _run_one(jc, REVIEW / "m5_jumpcut.mp4", tmp, keep_video=True)
        )
    print(f"{'file':<34} {'in s':>8} {'out s':>8} {'removed s':>9}"
          f" {'cuts':>5}")
    for r in rows:
        print(
            f"{r['path'].name:<34} {r['in_s']:>8.2f} {r['out_s']:>8.2f}"
            f" {r['removed_s']:>9.2f} {r['cuts']:>5}"
        )
    return rows


def main() -> int:
    recordings = sorted(REAL.glob("*.m4a"))
    if not recordings:
        print(f"no real recordings in {REAL} — nothing to do")
        return 1
    REVIEW.mkdir(parents=True, exist_ok=True)
    device = _job_device()
    print(f"job device: {device}")
    if "--m8" in sys.argv[1:]:
        _m8(recordings, device)
        return 0
    rows, rows_b, rows7 = [], [], []
    with tempfile.TemporaryDirectory(prefix="m6_review_") as td:
        tmp = Path(td)
        model = _whisper_model()
        for rec in recordings:
            print(f"processing {rec.name} (podcast + keep-silence)…")
            rows.append(_m6_one(rec, tmp, device))
            print(f"processing {rec.name} (m6b podcast + breaths)…")
            rows_b.append(_m6b_one(rec, tmp, device, model))
        del model
        if "--m7" in sys.argv[1:]:
            for rec in recordings:
                print(f"processing {rec.name} (m7 separation)…")
                rows7.append(_m7_one(rec, tmp, device))
    _m6_report(rows)
    _m6b_report(rows_b)
    if "--m7" in sys.argv[1:]:
        _m7_report(rows7)
    if "--m5" in sys.argv[1:]:
        _m5(recordings)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
