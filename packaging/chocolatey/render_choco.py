"""packaging/chocolatey/render_choco.py — render the choco templates from
release-info.toml (M10d wave 2).

    uv run python packaging/chocolatey/render_choco.py \
        --sha256 <setup.exe sha256> [--out dist_pkg/choco]

    # or pull the sum straight from a SHA256SUMS.txt:
    uv run python packaging/chocolatey/render_choco.py \
        --sums dist_pkg/SHA256SUMS.txt [--out DIR]

Fills {{VERSION}} {{REPO}} {{SETUP_ASSET}} {{SHA256}} {{APP_GUID}} in
safa.nuspec + tools/*.ps1 and writes the rendered package files to
--out (default: overwrite in place is NOT allowed — an explicit out dir
keeps the committed templates pristine).
"""
from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHOCO = ROOT / "packaging" / "chocolatey"
TOKENS = ("{{VERSION}}", "{{REPO}}", "{{SETUP_ASSET}}", "{{SHA256}}",
          "{{APP_GUID}}")


def _info() -> dict:
    return tomllib.loads((ROOT / "release-info.toml").read_text("utf-8"))


def version() -> str:
    return tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))[
        "project"]["version"]


def setup_asset(ver: str) -> str:
    return _info()["assets"]["online_setup"].format(ver=ver)


def sum_from_sums_file(sums: Path, name: str) -> str | None:
    for line in sums.read_text("utf-8").splitlines():
        parts = line.strip().split()
        if len(parts) == 2 and parts[1].lstrip("*./") == name:
            return parts[0].lower()
    return None


def render(version: str, sha256: str, out_dir: Path) -> list[Path]:
    info = _info()
    values = {
        "{{VERSION}}": version,
        "{{REPO}}": info["app"]["repo_url"],
        "{{SETUP_ASSET}}": setup_asset(version),
        "{{SHA256}}": sha256,
        "{{APP_GUID}}": info["guid"]["online"],
    }
    written = []
    for src in (CHOCO / "safa.nuspec",
                    CHOCO / "tools" / "chocolateyinstall.ps1",
                    CHOCO / "tools" / "chocolateyuninstall.ps1"):
        text = src.read_text(encoding="utf-8-sig")
        for k, v in values.items():
            text = text.replace(k, v)
        dst = out_dir / src.relative_to(CHOCO)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(text, encoding="utf-8-sig"
                       if src.suffix == ".ps1" else "utf-8")
        written.append(dst)
    return written


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sha256", default=None,
                    help="sha256 of the online setup.exe")
    ap.add_argument("--sums", type=Path, default=None,
                    help="read the setup.exe hash from a SHA256SUMS.txt")
    ap.add_argument("--version", default=None)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)

    ver = args.version or version()
    sha = args.sha256
    if sha is None and args.sums:
        sha = sum_from_sums_file(args.sums, setup_asset(ver))
    if not sha:
        ap.error("no sha256: pass --sha256 or --sums SHA256SUMS.txt")
    written = render(ver, sha.lower(), args.out)
    for p in written:
        print(p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
