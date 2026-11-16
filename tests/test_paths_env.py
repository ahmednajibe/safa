"""M10b: environment overrides for ``backend.core.paths``.

``SOUND_SYSTEM_MODELS_DIR`` relocates the model cache root — the portable
package smoke test uses it to point a packaged copy at an existing
``models/`` tree so nothing re-downloads. Paths are computed at import
time, so the honest test is a subprocess with a controlled env.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read_paths(env_extra: dict[str, str]) -> str:
    env = dict(os.environ)
    env.update(env_extra)
    env["PYTHONUTF8"] = "1"
    out = subprocess.run(
        [
            sys.executable,
            "-c",
            "from backend.core import paths; print(paths.MODELS_DIR)",
        ],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        env=env,
        check=True,
    )
    return out.stdout.strip()


def test_models_dir_env_override(tmp_path):
    custom = tmp_path / "custom models"
    got = Path(_read_paths({"SOUND_SYSTEM_MODELS_DIR": str(custom)}))
    assert got == custom


def test_models_dir_default_when_env_unset():
    env = dict(os.environ)
    env.pop("SOUND_SYSTEM_MODELS_DIR", None)
    env["PYTHONUTF8"] = "1"
    out = subprocess.run(
        [
            sys.executable,
            "-c",
            "from backend.core import paths; print(paths.MODELS_DIR)",
        ],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        env=env,
        check=True,
    )
    assert Path(out.stdout.strip()) == ROOT / "models"
