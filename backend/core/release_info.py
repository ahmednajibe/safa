"""Release metadata — canonical reader for ``release-info.toml`` (M10c).

Scripts, installers, manifests, docs and tests all derive names/URLs from
this module so they can never drift. The app version itself still lives
in ``pyproject.toml`` (single owner); this module joins the two.

CLI (for scripts):
  python -m backend.core.release_info             → every rendered asset
  python -m backend.core.release_info <key>       → one value
  python -m backend.core.release_info version     → app version
  python -m backend.core.release_info repo        → repository URL
"""
from __future__ import annotations

import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load() -> dict:
    return tomllib.loads((ROOT / "release-info.toml").read_text("utf-8"))


_INFO = _load()
APP_NAME: str = _INFO["app"]["name"]
APP_NAME_AR: str = _INFO["app"]["name_ar"]
APP_ID: str = _INFO["app"]["id"]
APP_TITLE: str = _INFO["app"]["title"]
REPO_URL: str = _INFO["app"]["repo_url"]
DEFAULT_PORT: int = int(_INFO["app"]["port"])
DRIVER_MIN: int = int(_INFO["app"]["driver_min"])
ASSETS: dict[str, str] = dict(_INFO["assets"])
MODEL_PACKS: dict[str, list[str]] = {
    k: list(v) for k, v in _INFO["model_packs"].items()
}
GUIDS: dict[str, str] = dict(_INFO["guid"])


def version() -> str:
    """App version from pyproject.toml (the single version owner)."""
    data = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))
    return data["project"]["version"]


VERSION = version()


def asset(key: str, ver: str | None = None, **fmt) -> str:
    """Render an asset name template: ``asset("online_setup")`` →
    ``Safa-0.1.0-win-x64-setup.exe``. ``{ver}`` defaults to the app
    version; extra placeholders (``{n}``, ``{nnn}``) come from ``fmt``."""
    return ASSETS[key].format(ver=ver or VERSION, **fmt)


def asset_names(ver: str | None = None) -> dict[str, str]:
    """Every asset name rendered at ``ver`` (templates needing {n}/{nnn}
    are left unrendered — they're per-volume patterns)."""
    out: dict[str, str] = {}
    for key in ASSETS:
        try:
            out[key] = asset(key, ver)
        except KeyError:
            out[key] = ASSETS[key]  # per-volume pattern
    return out


def _cli(argv: list[str]) -> int:
    if not argv:
        for k, v in sorted(asset_names().items()):
            print(f"{k}={v}")
        return 0
    key = argv[0]
    if key == "version":
        print(VERSION)
    elif key == "repo":
        print(REPO_URL)
    elif key == "name":
        print(APP_NAME)
    elif key in ASSETS:
        fmt = {}
        for a in argv[1:]:
            k, _, v = a.partition("=")
            fmt[k] = v
        print(asset(key, **fmt))
    elif key == "model_packs":
        for k, v in MODEL_PACKS.items():
            print(f"{k}={','.join(v)}")
    elif key == "guid":
        for k, v in GUIDS.items():
            print(f"{k}={v}")
    else:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
