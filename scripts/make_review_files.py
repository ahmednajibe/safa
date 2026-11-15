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

``--m5`` also regenerates the M5 files:
  - ``m5_<recording stem>_cut.wav`` per real recording (silence defaults)
  - ``m5_jumpcut.mp4`` (testsrc video + speech_silences.wav, keep_video)

Run:  uv run python scripts/make_review_files.py [--m5]
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
    rows, rows_b = [], []
    with tempfile.TemporaryDirectory(prefix="m6_review_") as td:
        tmp = Path(td)
        model = _whisper_model()
        for rec in recordings:
            print(f"processing {rec.name} (podcast + keep-silence)…")
            rows.append(_m6_one(rec, tmp, device))
            print(f"processing {rec.name} (m6b podcast + breaths)…")
            rows_b.append(_m6b_one(rec, tmp, device, model))
        del model
    _m6_report(rows)
    _m6b_report(rows_b)
    if "--m5" in sys.argv[1:]:
        _m5(recordings)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
