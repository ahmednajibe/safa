"""Generate the M5 review files in ``data/review/`` (explicit, not pytest).

Outputs:
- ``m5_<recording stem>_cut.wav`` for each real recording — silence op
  with defaults (``{"action": "remove"}``) through the real pipeline.
- ``m5_jumpcut.mp4`` — testsrc video + ``speech_silences.wav`` audio
  through the same op with ``keep_video=True``.

Also prints the per-recording numbers the plan's Task 5 asks for:
input duration, output duration, seconds removed, number of cuts.

Run:  uv run python scripts/make_review_files.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.core.ffmpeg import find_ffmpeg  # noqa: E402
from backend.media.probe import probe  # noqa: E402
from backend.media.timeline import Timeline  # noqa: E402
from backend.pipeline.runner import run_pipeline  # noqa: E402
from backend.pipeline.types import OutputSpec  # noqa: E402

REAL = ROOT / "tests" / "fixtures" / "real"
SPEECH_SIL = ROOT / "tests" / "fixtures" / "generated" / "speech_silences.wav"
REVIEW = ROOT / "data" / "review"
FPS = 25.0


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


def main() -> int:
    recordings = sorted(REAL.glob("*.m4a"))
    if not recordings:
        print(f"no real recordings in {REAL} — nothing to do")
        return 1
    REVIEW.mkdir(parents=True, exist_ok=True)
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
