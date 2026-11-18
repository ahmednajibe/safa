"""scripts/build-wheel.py — stage the safa-audio payload and run uv build
(M10d §9 wave 2: PyPI bootstrap wheel).

    uv run python scripts/build-wheel.py [--no-build] [--out-dir dist_pkg]

The wheel carries ONLY: backend/, frontend/dist/, scripts/prefetch_models.py,
release-info.toml, README/LICENSE/notices and the frozen hashed
requirements-<cpu|cu124>.txt locks — never models/, data/, tests/,
.orchestrator/, node_modules/, graphify-out/ or tools/ binaries.

Version is read from the root pyproject (single owner) and stamped into
packaging/pypi/pyproject.toml before building.
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "packaging" / "pypi"
APP = PKG / "safa_bootstrap" / "app"

# payload whitelist — everything else in the repo must NOT reach the wheel
FILES = (
    "release-info.toml", "pyproject.toml", "README.md", "README.ar.md",
    "LICENSE", "THIRD_PARTY_NOTICES.txt",
)
DIRS = (("backend", "backend"), ("frontend/dist", "frontend/dist"))
SCRIPTS = ("prefetch_models.py",)
_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_tmp")


def root_version() -> str:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))
    return data["project"]["version"]


def stamp_version(pyproject: Path, version: str) -> None:
    text = pyproject.read_text(encoding="utf-8")
    new, n = re.subn(r'(?m)^version = "[^"]*"$',
                     f'version = "{version}"', text, count=1)
    if n != 1:
        raise RuntimeError("version line not found in packaging/pypi/pyproject.toml")
    if new != text:
        pyproject.write_text(new, encoding="utf-8")


def stage_payload(app: Path = APP) -> Path:
    """Fresh copy of the whitelisted payload into safa_bootstrap/app/."""
    if app.exists():
        shutil.rmtree(app)
    app.mkdir(parents=True)
    for src, dst in DIRS:
        s = ROOT / src
        if not s.is_dir():
            raise RuntimeError(f"payload source missing: {s} "
                               "(build the frontend first: npm --prefix frontend run build)")
        shutil.copytree(s, app / dst, ignore=_IGNORE)
    for f in FILES:
        shutil.copy2(ROOT / f, app / f)
    (app / "scripts").mkdir(exist_ok=True)
    for f in SCRIPTS:
        shutil.copy2(ROOT / "scripts" / f, app / "scripts" / f)
    return app


def export_requirements(app: Path = APP) -> list[Path]:
    """Frozen + hashed locks for both variants — the same exports the
    Inno installer provisions from (uv export keeps --hash lines)."""
    sys.path.insert(0, str(ROOT / "scripts"))
    from export_requirements import export  # uv export + cpu ORT swap

    return [export(extra, app / f"requirements-{extra}.txt")
            for extra in ("cpu", "cu124")]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--no-build", action="store_true",
                    help="stage payload only (no uv build)")
    ap.add_argument("--out-dir", default="dist_pkg")
    args = ap.parse_args(argv)

    version = root_version()
    stamp_version(PKG / "pyproject.toml", version)
    app = stage_payload()
    reqs = export_requirements(app)
    for req in reqs:
        if "--hash=" not in req.read_text("utf-8"):
            raise RuntimeError(f"{req.name} lost its hashes")
    print(f"payload staged at {app} (version {version})")

    if args.no_build:
        return 0
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["uv", "build", "--out-dir", str(out.resolve())],
        cwd=PKG, check=True)
    print(f"wheel built in {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
