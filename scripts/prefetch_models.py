"""Prefetch ML model files under ``models/`` (SPEC §8 offline story).

M7 scope: the two audio-separator models plus every metadata JSON the
library downloads at ``load_model`` time — driving the real load path
fetches exactly the files the engine needs (weights, ``htdemucs.yaml``,
``download_checks.json``, ``vr_model_data.json``, ``mdx_model_data.json``),
nothing hardcoded to rot.

Usage:  uv run python scripts/prefetch_models.py [model ...]
        (default: the separation + whisper models; ``--all`` for every
        known model)
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.core.models_manifest import MODELS, model_dir  # noqa: E402

SEPARATOR_MODELS = ("htdemucs", "kim_vocal_2")
WHISPER_MODELS = ("whisper_small", "whisper_turbo")


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


def prefetch_whisper(model: str) -> None:
    """Build the whisper model once on CPU — faster-whisper's
    ``snapshot_download`` fills the HF layout under ``models/whisper/``
    with exactly the files runtime needs."""
    t0 = time.monotonic()
    from faster_whisper import WhisperModel  # noqa: E402 — worker script

    from backend.media.transcribe import _MANIFEST_KEYS, MODEL_IDS

    model_key = next(
        k for k, v in _MANIFEST_KEYS.items() if v == model
    )
    m = WhisperModel(
        MODEL_IDS[model_key],
        device="cpu",
        compute_type="int8",
        download_root=str(model_dir(model)),
    )
    del m
    print(f"{model}: ready ({time.monotonic() - t0:.0f}s)")


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    names = args or list(SEPARATOR_MODELS) + list(WHISPER_MODELS)
    if "--all" in sys.argv:
        names = list(MODELS)
    for name in names:
        if name not in MODELS:
            print(f"unknown model {name!r} — choices: {sorted(MODELS)}")
            return 2
        if name in SEPARATOR_MODELS:
            prefetch_separator(name)
        elif name in WHISPER_MODELS:
            prefetch_whisper(name)
        else:
            print(f"{name}: prefetch not implemented yet (lazy at first use)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
