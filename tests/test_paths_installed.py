"""M10c §5: installed vs portable data locations.

Mode resolution (computed once at import):

1. ``SOUND_SYSTEM_DATA_DIR`` — always wins (installers, Docker, tests).
2. ``installed.json`` next to the app (``<root>/installed.json`` written by
   the Inno installer) → installed mode: ``DATA_DIR`` = the file's
   ``data_dir`` (absolute, may contain spaces/Arabic), ``MODELS_DIR`` =
   ``<data_dir>/models``.
3. ``portable.txt`` marker or nothing at all (dev checkout, portable zip)
   → ``DATA_DIR`` = ``<root>/data``, ``MODELS_DIR`` = ``<root>/models``.

``SOUND_SYSTEM_MODELS_DIR`` always overrides the models root on its own.
Corrupt ``installed.json`` falls back to portable/dev behavior — a broken
marker must never break startup.

``paths.ROOT`` comes from ``__file__``, so the honest probe copies
``paths.py`` into a fake app tree and imports it there in a subprocess.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATHS_SRC = ROOT / "backend" / "core" / "paths.py"

PROBE = r"""
import importlib.util, sys
spec = importlib.util.spec_from_file_location("paths", sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
print(m.DATA_DIR)
print(m.MODELS_DIR)
print(m.tmp_dir())
"""


def _fake_root(tmp_path: Path) -> Path:
    """Fake app tree: <tmp>/app/backend/core/paths.py → ROOT = <tmp>/app."""
    app = tmp_path / "app dir"
    (app / "backend" / "core").mkdir(parents=True)
    shutil.copy(PATHS_SRC, app / "backend" / "core" / "paths.py")
    return app


def _read(app: Path, env_extra: dict[str, str]) -> tuple[Path, Path, Path]:
    env = dict(os.environ)
    env.update(env_extra)
    for k in ("SOUND_SYSTEM_DATA_DIR", "SOUND_SYSTEM_MODELS_DIR"):
        if k not in env_extra:
            env.pop(k, None)
    env["PYTHONUTF8"] = "1"
    paths_py = app / "backend" / "core" / "paths.py"
    out = subprocess.run(
        [sys.executable, "-c", PROBE, str(paths_py)],
        capture_output=True, text=True, encoding="utf-8",
        cwd=str(app), env=env, check=True,
    )
    lines = out.stdout.strip().splitlines()
    return Path(lines[0]), Path(lines[1]), Path(lines[2])


def test_portable_marker_uses_package_local_data(tmp_path):
    app = _fake_root(tmp_path)
    (app / "portable.txt").write_text("portable", encoding="utf-8")
    data, models, tmp = _read(app, {})
    assert data == app / "data"
    assert models == app / "models"
    assert tmp == app / "data" / "tmp"


def test_dev_checkout_without_markers_behaves_like_portable(tmp_path):
    app = _fake_root(tmp_path)
    data, models, _ = _read(app, {})
    assert data == app / "data"
    assert models == app / "models"


def test_installed_json_points_data_elsewhere(tmp_path):
    app = _fake_root(tmp_path)
    chosen = tmp_path / "بيانات المستخدم" / "Safa Data"
    (app / "installed.json").write_text(
        json.dumps({"data_dir": str(chosen)}), encoding="utf-8")
    data, models, tmp = _read(app, {})
    assert data == chosen
    assert models == chosen / "models"
    assert tmp == chosen / "tmp"


def test_data_dir_env_beats_installed_json(tmp_path):
    app = _fake_root(tmp_path)
    (app / "installed.json").write_text(
        json.dumps({"data_dir": str(tmp_path / "from-json")}),
        encoding="utf-8")
    env_dir = tmp_path / "env override"
    data, models, _ = _read(app, {"SOUND_SYSTEM_DATA_DIR": str(env_dir)})
    assert data == env_dir
    assert models == env_dir / "models"


def test_models_dir_env_beats_everything(tmp_path):
    app = _fake_root(tmp_path)
    chosen = tmp_path / "installed data"
    (app / "installed.json").write_text(
        json.dumps({"data_dir": str(chosen)}), encoding="utf-8")
    models_env = tmp_path / "نماذج منفصلة"
    data, models, _ = _read(app, {
        "SOUND_SYSTEM_DATA_DIR": str(tmp_path / "env data"),
        "SOUND_SYSTEM_MODELS_DIR": str(models_env),
    })
    assert data == tmp_path / "env data"
    assert models == models_env


def test_corrupt_installed_json_falls_back_to_portable(tmp_path):
    app = _fake_root(tmp_path)
    (app / "installed.json").write_text("{not json!!", encoding="utf-8")
    data, models, _ = _read(app, {})
    assert data == app / "data"
    assert models == app / "models"


def test_installed_json_missing_data_dir_falls_back(tmp_path):
    app = _fake_root(tmp_path)
    (app / "installed.json").write_text(
        json.dumps({"version": 1}), encoding="utf-8")
    data, _, _ = _read(app, {})
    assert data == app / "data"


def test_installed_json_optional_models_dir(tmp_path):
    app = _fake_root(tmp_path)
    data_dir = tmp_path / "my data"
    models_dir = tmp_path / "my models على القرص د"
    (app / "installed.json").write_text(
        json.dumps({"data_dir": str(data_dir), "models_dir": str(models_dir)}),
        encoding="utf-8")
    _, models, _ = _read(app, {})
    assert models == models_dir


# ---- .safa-data uninstall marker ---------------------------------------------------
#
# The Inno uninstaller only offers to delete the data folder when
# ``<data>/.safa-data`` exists (protects a user-chosen dir like Documents
# from DelTree). The installer writes it, and ``paths.ensure_data_marker``
# re-creates it on app start when an upgrade wiped it.

MARKER_PROBE = r"""
import importlib.util, sys
spec = importlib.util.spec_from_file_location("paths", sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
m.ensure_data_marker()
print(m.DATA_DIR)
"""


def _ensure_marker(app: Path, env_extra: dict[str, str]) -> Path:
    env = dict(os.environ)
    env.update(env_extra)
    for k in ("SOUND_SYSTEM_DATA_DIR", "SOUND_SYSTEM_MODELS_DIR"):
        if k not in env_extra:
            env.pop(k, None)
    env["PYTHONUTF8"] = "1"
    paths_py = app / "backend" / "core" / "paths.py"
    out = subprocess.run(
        [sys.executable, "-c", MARKER_PROBE, str(paths_py)],
        capture_output=True, text=True, encoding="utf-8",
        cwd=str(app), env=env, check=True,
    )
    return Path(out.stdout.strip().splitlines()[0])


def test_ensure_data_marker_installed_mode(tmp_path):
    app = _fake_root(tmp_path)
    chosen = tmp_path / "بيانات" / "Safa Data"
    (app / "installed.json").write_text(
        json.dumps({"data_dir": str(chosen)}), encoding="utf-8")
    data = _ensure_marker(app, {})
    marker = chosen / ".safa-data"
    assert data == chosen
    assert marker.is_file(), "installed mode must drop .safa-data"
    assert "safa" in marker.read_text(encoding="utf-8").lower()


def test_ensure_data_marker_skipped_when_portable(tmp_path):
    app = _fake_root(tmp_path)
    (app / "portable.txt").write_text("portable", encoding="utf-8")
    (app / "installed.json").write_text(
        json.dumps({"data_dir": str(tmp_path / "elsewhere")}),
        encoding="utf-8")
    data = _ensure_marker(app, {})
    assert data == app / "data"
    assert not (tmp_path / "elsewhere" / ".safa-data").exists()
    assert not (app / "data" / ".safa-data").exists()


def test_ensure_data_marker_skipped_on_env_override(tmp_path):
    """SOUND_SYSTEM_DATA_DIR (Docker/tests) is not the installer's chosen
    folder — marking it would mislabel foreign dirs as Safa data."""
    app = _fake_root(tmp_path)
    (app / "installed.json").write_text(
        json.dumps({"data_dir": str(tmp_path / "installed data")}),
        encoding="utf-8")
    env_dir = tmp_path / "env data"
    data = _ensure_marker(app, {"SOUND_SYSTEM_DATA_DIR": str(env_dir)})
    assert data == env_dir
    assert not (env_dir / ".safa-data").exists()
