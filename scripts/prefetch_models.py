"""Prefetch ML model files under ``models/`` (SPEC §8 offline story).

M7 scope: the two audio-separator models plus every metadata JSON the
library downloads at ``load_model`` time — driving the real load path
fetches exactly the files the engine needs (weights, ``htdemucs.yaml``,
``download_checks.json``, ``vr_model_data.json``, ``mdx_model_data.json``),
nothing hardcoded to rot.

Usage:  uv run python scripts/prefetch_models.py [model ...]
        (default: the separation models; ``--all`` for every known model)
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.core.models_manifest import MODELS, model_dir  # noqa: E402

SEPARATOR_MODELS = ("htdemucs", "kim_vocal_2")


def _dir_snapshot(d: Path) -> set[str]:
    return {p.name for p in d.iterdir()} if d.is_dir() else set()


def prefetch_separator(model: str) -> list[str]:
    """Load ``model`` once on CPU — the library downloads every missing
    weight/metadata file into ``model_dir`` itself. Returns new files."""
    from backend.media import separator

    d = model_dir(model)
    before = _dir_snapshot(d)
    t0 = time.monotonic()
    # device='cpu' is enough — files land the same either way and this
    # keeps prefetch usable on machines without a GPU.
    eng = separator._make_engine(model, "cpu", {"voice"}, 7)
    eng.close()
    after = _dir_snapshot(d)
    got = sorted(after - before)
    print(
        f"{model}: {'already cached' if not got else 'downloaded'} "
        f"({time.monotonic() - t0:.0f}s)"
    )
    for name in got:
        print(f"  + {name}")
    return got


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    names = args or list(SEPARATOR_MODELS)
    if "--all" in sys.argv:
        names = list(MODELS)
    for name in names:
        if name not in MODELS:
            print(f"unknown model {name!r} — choices: {sorted(MODELS)}")
            return 2
        if name in SEPARATOR_MODELS:
            prefetch_separator(name)
        else:
            print(f"{name}: prefetch not implemented yet (lazy at first use)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
