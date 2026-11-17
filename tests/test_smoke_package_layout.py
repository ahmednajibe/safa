"""smoke_package.check_layout: installed trees need no portable.txt."""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "smoke_package", ROOT / "scripts" / "smoke_package.py")
smoke_package = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(smoke_package)


def _tree(base: Path, *, portable: bool, installed: bool) -> Path:
    for rel in smoke_package._REQUIRED:
        if rel == "portable.txt" and not portable:
            continue
        p = base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x", encoding="utf-8")
    if installed:
        (base / "installed.json").write_text("{}", encoding="utf-8")
    return base


def test_portable_tree_requires_marker(tmp_path):
    root = _tree(tmp_path / "p", portable=False, installed=False)
    assert smoke_package.check_layout(root) == ["portable.txt"]


def test_installed_tree_without_marker_ok(tmp_path):
    root = _tree(tmp_path / "i", portable=False, installed=True)
    assert smoke_package.check_layout(root) == []


def test_health_only_flag_parses():
    import inspect
    assert "health_only" in inspect.signature(smoke_package.smoke).parameters
