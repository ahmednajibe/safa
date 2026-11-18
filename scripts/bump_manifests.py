"""scripts/bump_manifests.py — fill the committed package-manager
manifests with the real version + sha256 sums after a release is
published (winget, Scoop, Homebrew, AUR).

    uv run python scripts/bump_manifests.py --sums SHA256SUMS.txt \
        [--version 0.1.1] [--date 2026-11-18]

--version defaults to pyproject [project].version; --date defaults to
today (UTC). Asset names come from release-info.toml [assets] rendered
with {ver}; download URLs are built from [app] repo_url.

Every manifest is rewritten IN PLACE, anchored on keys (never on
placeholder text), so re-running is a no-op. The script refuses
(exit 2, writes NOTHING) when a needed asset is missing from the sums
file or a hash is not 64 hex chars — all checks run before any write.

Tests pass ``root=`` to run against a copied tree; everything else uses
the repository root this file lives in.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_BOM = b"\xef\xbb\xbf"

# the four assets whose sha256 the manifests consume
NEEDED = ("online_setup", "cpu_portable_zip", "macos_tarball",
          "linux_cpu_tarball")
BUMP = "scripts/bump_manifests.py --sums SHA256SUMS.txt"
WINGET_NOTE = ("# InstallerUrl/InstallerSha256/ReleaseDate are filled by\n"
               f"# {BUMP} after a release is published.")
BREW_NOTE = (f"# url + sha256 are filled by {BUMP}\n"
             "# after a release is published.")
AUR_NOTE = (f"# pkgver + sha256sums are filled by {BUMP}\n"
            "# after a release is published.")


class BumpError(Exception):
    """A manifest lacks an anchor the script must rewrite."""


def _read(path: Path) -> tuple[str, str, bytes]:
    """File text normalized to LF, its newline style, and its BOM bytes —
    each manifest keeps the encoding/line-endings it already has."""
    raw = path.read_bytes()
    bom = _BOM if raw.startswith(_BOM) else b""
    text = raw[len(bom):].decode("utf-8")
    nl = "\r\n" if "\r\n" in text else "\n"
    return text.replace("\r\n", "\n"), nl, bom


def _write(path: Path, text: str, nl: str, bom: bytes) -> bool:
    """Write only when bytes differ (idempotent: second run is a no-op)."""
    data = bom + text.replace("\n", nl).encode("utf-8")
    if data == path.read_bytes():
        return False
    path.write_bytes(data)
    return True


def sum_from_sums_file(sums: Path, name: str) -> str | None:
    """Same lookup as packaging/chocolatey/render_choco.py — handles
    ``<hash>  <name>``, ``<hash>  *<name>`` and ``<hash>  ./<name>``."""
    for line in sums.read_text("utf-8").splitlines():
        parts = line.strip().split()
        if len(parts) == 2 and parts[1].lstrip("*./") == name:
            return parts[0]
    return None


def _fix_sha_comment(text: str, canonical: str) -> str:
    """Normalize the stale 'Replace <sha256…>' comment block (plus any
    comment continuation lines) to the canonical bump-manifests note.
    Anchored on the sha256 comment, never on field text; the canonical
    note contains neither 'replace' nor '<sha256', so it is idempotent."""
    return re.sub(r"(?im)^#[^\n]*replace[^\n]*<sha256[^\n]*(?:\n#[^\n]*)*",
                  canonical, text)


def _sub(text: str, pattern: str, repl: str, where: str, key: str) -> str:
    """re.sub anchored on a key; missing anchor is a BumpError, never a
    silent pass."""
    new, n = re.subn(pattern, repl, text, flags=re.M)
    if n == 0:
        raise BumpError(f"{where}: no '{key}' line to rewrite")
    return new


def _winget(root: Path, ver: str, date: str, url_of, hashes) -> list:
    files = sorted((root / "packaging" / "winget").glob("*.yaml"))
    if not files:
        raise BumpError("packaging/winget: no *.yaml manifests found")
    out = []
    for p in files:
        text, nl, bom = _read(p)
        text = _sub(text, r'^(\s*PackageVersion:\s*)"[^"]*"',
                    rf'\g<1>"{ver}"', p.name, "PackageVersion")
        if p.name == "Safa.Safa.installer.yaml":
            h = hashes.get("online_setup", "0" * 64).upper()
            text = _fix_sha_comment(text, WINGET_NOTE)
            text = _sub(text, r'^(\s*ReleaseDate:\s*)"[^"]*"',
                        rf'\g<1>"{date}"', p.name, "ReleaseDate")
            text = _sub(text, r'^(\s*InstallerUrl:\s*)\S+',
                        rf'\g<1>{url_of("online_setup")}',
                        p.name, "InstallerUrl")
            text = _sub(text, r'^(\s*InstallerSha256:\s*)"[^"]*"',
                        rf'\g<1>"{h}"', p.name, "InstallerSha256")
        out.append((p, text, nl, bom))
    return out


def _scoop(root: Path, ver: str, repo: str, sums_name: str,
           url_of, hashes) -> list:
    p = root / "packaging" / "scoop" / "safa.json"
    text, nl, bom = _read(p)
    try:
        data = json.loads(text)
        arch = data["architecture"]["64bit"]
        if not isinstance(arch, dict):
            raise TypeError("architecture.64bit is not an object")
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        raise BumpError(f"{p.name}: not the expected scoop manifest ({e})")
    data["version"] = ver
    arch["url"] = url_of("cpu_portable_zip")
    arch["hash"] = hashes.get("cpu_portable_zip", "0" * 64)
    # Scoop reads the file's hash from the sums file on autoupdate.
    au64 = data.setdefault("autoupdate", {}).setdefault(
        "architecture", {}).setdefault("64bit", {})
    au64["hash"] = {"url": f"{repo}/releases/download/"
                           f"v$version/{sums_name}"}
    return [(p, json.dumps(data, indent=4, ensure_ascii=False) + "\n",
            nl, bom)]


def _homebrew(root: Path, url_of, hashes) -> list:
    p = root / "packaging" / "homebrew" / "safa.rb"
    text, nl, bom = _read(p)
    text = _fix_sha_comment(text, BREW_NOTE)
    text = _sub(text, r'^(\s*url\s+)"[^"]*"',
                rf'\g<1>"{url_of("macos_tarball")}"', p.name, "url")
    text = _sub(text, r'^(\s*sha256\s+)"[^"]*"',
                rf'\g<1>"{hashes.get("macos_tarball", "0" * 64)}"',
                p.name, "sha256")
    # no `version` stanza — Homebrew infers it from the url.
    return [(p, text, nl, bom)]


def _aur(root: Path, ver: str, hashes) -> list:
    p = root / "packaging" / "aur" / "PKGBUILD"
    text, nl, bom = _read(p)
    text = _fix_sha_comment(text, AUR_NOTE)
    text = _sub(text, r'^pkgver=.*', f'pkgver={ver}', p.name, "pkgver")
    text = _sub(text, r'^pkgrel=.*', 'pkgrel=1', p.name, "pkgrel")
    h = hashes.get("linux_cpu_tarball", "0" * 64)
    text = _sub(text, r"^sha256sums=\('[^']*'\)",
                f"sha256sums=('{h}')", p.name, "sha256sums")
    return [(p, text, nl, bom)]


def main(argv: list[str] | None = None,
         root: Path | str | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sums", type=Path, required=True,
                    help="SHA256SUMS.txt published with the release")
    ap.add_argument("--version", default=None,
                    help="default: pyproject [project].version")
    ap.add_argument("--date", default=None,
                    help="winget ReleaseDate, YYYY-MM-DD (default: today UTC)")
    args = ap.parse_args(argv)
    root = Path(root if root is not None else ROOT).resolve()

    errors: list[str] = []

    # ---- config: pyproject version + release-info assets -------------
    info, proj = {}, {}
    try:
        info = tomllib.loads(
            (root / "release-info.toml").read_text("utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as e:
        errors.append(f"release-info.toml: {e}")
    try:
        proj = tomllib.loads((root / "pyproject.toml").read_text("utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as e:
        errors.append(f"pyproject.toml: {e}")

    ver = args.version or (proj.get("project") or {}).get("version")
    if not ver:
        errors.append("no version: pass --version or set "
                    "pyproject [project].version")
        ver = "0.0.0"  # keeps error paths renderable; nothing is written
    date = args.date or dt.datetime.now(dt.timezone.utc).date().isoformat()
    try:
        dt.date.fromisoformat(date)
    except ValueError:
        errors.append(f"--date {date!r} is not YYYY-MM-DD")

    repo = (info.get("app") or {}).get("repo_url", "").rstrip("/")
    if not repo:
        errors.append("release-info.toml: missing [app] repo_url")
    assets = info.get("assets") or {}
    names: dict[str, str] = {}
    for key in NEEDED:
        tmpl = assets.get(key)
        if tmpl is None:
            errors.append(f"release-info.toml: missing [assets] {key}")
        else:
            names[key] = tmpl.format(ver=ver)
    sums_name = assets.get("sha256sums", "SHA256SUMS.txt").format(ver=ver)

    # ---- sums validation ---------------------------------------------
    hashes: dict[str, str] = {}
    if not args.sums.is_file():
        errors.append(f"sums file not found: {args.sums}")
    else:
        for key, name in names.items():
            h = sum_from_sums_file(args.sums, name)
            if h is None:
                errors.append(f"{args.sums.name}: no entry for {name}")
            elif not re.fullmatch(r"[0-9a-fA-F]{64}", h):
                errors.append(f"{args.sums.name}: hash for {name} is not "
                              f"64 hex chars: {h!r}")
            else:
                hashes[key] = h.lower()

    # ---- compute every new file before writing anything ---------------
    def url_of(key: str) -> str:
        return f"{repo}/releases/download/v{ver}/{names[key]}"
    writes: list = []
    for rel, fn in (
        ("packaging/winget", lambda: _winget(root, ver, date, url_of,
                                            hashes)),
        ("packaging/scoop/safa.json",
         lambda: _scoop(root, ver, repo, sums_name, url_of, hashes)),
        ("packaging/homebrew/safa.rb",
         lambda: _homebrew(root, url_of, hashes)),
        ("packaging/aur/PKGBUILD", lambda: _aur(root, ver, hashes)),
    ):
        if not (root / rel).exists():
            errors.append(f"manifest missing: {root / rel}")
            continue
        try:
            writes.extend(fn())
        except BumpError as e:
            errors.append(str(e))

    if errors:
        for e in errors:
            print(f"error: {e}", file=sys.stderr)
        print("refusing to write — no manifest touched", file=sys.stderr)
        return 2

    changed = [p for p, text, nl, bom in writes
               if _write(p, text, nl, bom)]
    for p in changed:
        print(f"updated {p.relative_to(root)}")
    if not changed:
        print("manifests already up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
