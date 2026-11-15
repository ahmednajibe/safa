"""Run the PRE-M4d pipeline for the ``--m4d`` review A/B.

Invoked by ``make_review_files.py --m4d`` (and the order experiment) as a
subprocess:

    python scripts/_m4d_old_run.py <worktree> <rec> <job_root> <device>

``worktree`` is a ``git worktree`` checkout of the pre-M4d commit — it is
placed on ``sys.path`` before any ``backend`` import so every op runs the
shipped implementation: silence@60 order, all-frame enhance calibration,
presence-EQ-before-compressor chain, and the M4c mouth-click detector.
Prints a JSON line ``{"out": <final wav>, "timeline": <path or null>}``.

Worktree lifecycle (it lives at ``data/m4d_old_worktree``, gitignored):

    # recreate — pin the PRE-M4d commit, not a moving HEAD:
    git worktree add data\\m4d_old_worktree <pre-M4d-commit-sha>
    # the reference used for the M4d review run was HEAD before the M4d
    # commit; find it later via `git log` (first parent of the M4d merge)
    # remove when done:
    git worktree remove data\\m4d_old_worktree

The script REFUSES to run if the worktree is missing or has no
``backend`` package — silently auto-creating it would check out whatever
HEAD happens to be (post-M4d after the milestone lands) and quietly
compare the pipeline against itself.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

worktree, rec_s, job_root_s, device = sys.argv[1:5]
main_root = sys.argv[5] if len(sys.argv) > 5 else None

if not (Path(worktree) / "backend" / "operations").is_dir():
    sys.exit(
        f"pre-M4d worktree missing or invalid: {worktree}\n"
        "recreate it against the pre-M4d commit:\n"
        "    git worktree add " + worktree + " <pre-M4d-commit-sha>\n"
        "see the module docstring for details"
    )

sys.path.insert(0, worktree)

rec = Path(rec_s)
job_root = Path(job_root_s)

# gitignored dirs aren't in the worktree — point models/ + caches at the
# main repo BEFORE anything captures paths.MODELS_DIR at import time.
if main_root:
    from backend.core import paths

    paths.MODELS_DIR = Path(main_root) / "models"
    paths.TOOLS_DIR = Path(main_root) / "tools"

from backend.core.gpu import setup_cuda_dlls  # noqa: E402
from backend.core.models_manifest import configure_model_env  # noqa: E402

configure_model_env()
setup_cuda_dlls()

from backend.core.capabilities import detect_capabilities  # noqa: E402
from backend.media.probe import probe  # noqa: E402
from backend.operations.registry import OPERATIONS, PRESETS  # noqa: E402
from backend.operations.rules import resolve, unavailable_code  # noqa: E402
from backend.pipeline.runner import run_pipeline  # noqa: E402
from backend.pipeline.types import OutputSpec  # noqa: E402

caps = detect_capabilities()
avail = {
    k: unavailable_code(s, caps.modules, caps.models) is None
    for k, s in OPERATIONS.items()
}
eff = resolve(dict(PRESETS["podcast"]["ops"]), avail)
p = probe(rec)
outs = run_pipeline(
    media_path=rec,
    probe=p,
    audio_stream_index=p.audio[0].index,
    ops=eff.ops,
    output=OutputSpec(audio_format="wav", keep_video=False),
    job_root=job_root,
    on_progress=lambda pr: None,
    should_cancel=lambda: False,
    device=device,
    display_name=rec.stem,
)
out = next(o for o in outs if o.kind == "audio")
tl = job_root / "output" / "timeline.json"
print(
    json.dumps(
        {
            "out": str(out.path),
            "timeline": str(tl) if tl.exists() else None,
            "ops": [op for op, _ in eff.ops],
        }
    )
)
