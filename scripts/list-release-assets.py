"""scripts/list-release-assets.py — pick publishable release assets out
of a dist_pkg tree (M10c review).

    python scripts/list-release-assets.py [dist_pkg] [--dest DIR] [--version V]

Every [assets] entry in release-info.toml becomes a glob: ``{ver}`` → the
app version (pyproject.toml or --version), ``{n}``/``{nnn}`` → ``*``.
Files matching any pattern — at ANY depth under dist_pkg (the GPU
portable pieces live in dist_pkg/gpu-portable/) — are printed (basename,
one per line, sorted). With ``--dest`` they are copied flat into that
directory. Staging dirs, extracted portable trees, logs and partial
downloads never match. Exits nonzero when nothing matched.
"""
from __future__ import annotations

import argparse
import fnmatch
import shutil
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_patterns(version: str | None) -> dict[str, str]:
    """asset key → glob pattern with the version rendered in."""
    info = tomllib.loads((ROOT / "release-info.toml").read_text("utf-8"))
    if version is None:
        proj = tomllib.loads(
            (ROOT / "pyproject.toml").read_text("utf-8"))
        version = proj["project"]["version"]
    out: dict[str, str] = {}
    for key, tmpl in info["assets"].items():
        pat = tmpl.replace("{ver}", version)
        pat = pat.replace("{nnn}", "*").replace("{n}", "*")
        out[key] = pat
    return out


def find_assets(dist: Path, patterns: dict[str, str]) -> dict[str, list[Path]]:
    """key → matched files (basename match, any depth under dist)."""
    files = sorted(p for p in dist.rglob("*") if p.is_file())
    out: dict[str, list[Path]] = {}
    for key, pat in patterns.items():
        hits = [p for p in files if fnmatch.fnmatchcase(p.name, pat)]
        if hits:
            out[key] = hits
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("dist", nargs="?", default="dist_pkg",
                    help="dist_pkg tree to scan (default: dist_pkg)")
    ap.add_argument("--dest", default=None,
                    help="copy matched files flat into this directory")
    ap.add_argument("--version", default=None,
                    help="override the {ver} placeholder (default: pyproject)")
    args = ap.parse_args(argv)

    dist = Path(args.dist)
    if not dist.is_dir():
        print(f"error: {dist} is not a directory", file=sys.stderr)
        return 2

    patterns = load_patterns(args.version)
    found = find_assets(dist, patterns)
    seen: dict[str, Path] = {}
    for key in sorted(found):
        for p in found[key]:
            seen.setdefault(p.name, p)  # first pattern wins on collisions
    for key in sorted(set(patterns) - set(found)):
        print(f"warning: no match for {key} ({patterns[key]})",
              file=sys.stderr)
    if not seen:
        print(f"error: no release assets under {dist}", file=sys.stderr)
        return 1

    if args.dest:
        dest = Path(args.dest)
        dest.mkdir(parents=True, exist_ok=True)
        for name, src in seen.items():
            shutil.copy2(src, dest / name)
    for name in sorted(seen):
        print(name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
