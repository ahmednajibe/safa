"""Prefetch ML model files under ``models/`` (SPEC §8 offline story,
M10 T3).

Every fetch goes through ``backend.core.model_download.download_model``
— the same allowlisted, pinned, sha-verified, ``.part``+atomic-rename
downloader the UI's download endpoint uses. DeepFilterNet3 is no longer
skipped: it is part of the core set below.

Usage:  uv run python scripts/prefetch_models.py [model ...]
        (default: the core set; ``--all`` for every known model)
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.core.model_download import download_model  # noqa: E402
from backend.core.models_manifest import (  # noqa: E402
    MODELS,
    configure_model_env,
)

# M10 T3: setup prefetches the core set; whisper_turbo + kim_vocal_2 are
# opt-in (--all / UI download button) because of their size.
CORE_MODELS = ("deepfilternet3", "htdemucs", "whisper_small", "silero_vad")

_LAST_FILE = [""]


def _report(model: str):
    def _p(ev: dict) -> None:
        name = ev.get("file", "")
        if name != _LAST_FILE[0]:
            _LAST_FILE[0] = name
            print(f"  {name}")
        pct = ev.get("pct")
        if pct is not None:
            # same-line progress: \r keeps the console readable
            print(f"    {pct:5.1f} %", end="\r", flush=True)

    return _p


def prefetch(model: str) -> bool:
    print(f"{model} ({MODELS[model].approx_mb} MB):")
    try:
        out = download_model(model, on_progress=_report(model))
    except Exception as exc:
        print(f"  FAILED: {exc}")
        return False
    if out.get("already"):
        print("  already present")
    else:
        print("  done")
    return True


def main() -> int:
    # Downloads ARE the point here — every other component stays offline.
    configure_model_env(offline=False)
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    names = args or list(CORE_MODELS)
    if "--all" in sys.argv:
        names = list(MODELS)
    ok = True
    for name in names:
        if name not in MODELS:
            print(f"unknown model {name!r} — choices: {sorted(MODELS)}")
            return 2
        if not prefetch(name):
            ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
