"""Export one variant's frozen requirements file from uv.lock.

    python scripts/export_requirements.py --extra cpu   -o requirements-cpu.txt
    python scripts/export_requirements.py --extra cu124 -o requirements-cu124.txt
    python scripts/export_requirements.py --extra cpu --no-hashes -o req.txt

Thin wrapper around ``uv export --frozen --no-dev --extra <x>`` — the ONE
place every packager (Inno installer, portable zip, unix tarballs, PyPI
payload) gets its requirements from.

The ``cpu`` export swaps onnxruntime-gpu (280 MB wheel, ~750 MB installed)
for the plain onnxruntime wheel (11–13 MB) on Windows/Linux x64. uv cannot
express that split itself: faster-whisper and silero-vad require
``onnxruntime``, both distributions install into the same ``onnxruntime/``
package dir, and ``tool.uv.override-dependencies`` cannot be gated by our
own extras (see pyproject.toml) — so the lock keeps onnxruntime-gpu on x64
for both extras and the shipped CPU install is fixed up here. The plain
onnxruntime 1.20.0 entry is already in uv.lock (darwin/aarch64 use it) with
every wheel's hash, so ``--require-hashes`` installs stay fully pinned.
Only shipped installs change; a dev ``uv sync --extra cpu`` still gets
onnxruntime-gpu, whose CPU provider behaves the same.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Wheels the plain-onnxruntime swap must be able to install with hashes.
_X64_WHEEL_TAGS = ("win_amd64", "manylinux_2_27_x86_64")


def _blocks(text: str) -> list[list[str]]:
    """Split a uv export into requirement blocks: a requirement line plus
    its ``--hash`` continuation and ``# via`` comment lines."""
    blocks: list[list[str]] = []
    for line in text.splitlines():
        starts_req = bool(line) and not line[0].isspace() and not line.startswith("#")
        if starts_req or not blocks:
            blocks.append([line])
        else:
            blocks[-1].append(line)
    return blocks


def _name(block: list[str]) -> str:
    m = re.match(r"([A-Za-z0-9_.-]+)", block[0])
    return m.group(1).lower() if m and not block[0].startswith("#") else ""


def _locked_ort_hashes(lock: Path) -> set[str]:
    data = tomllib.loads(lock.read_text(encoding="utf-8"))
    for pkg in data["package"]:
        if pkg["name"] == "onnxruntime":
            return {w["hash"].split(":", 1)[1] for w in pkg.get("wheels", [])
                    if any(t in w["url"] for t in _X64_WHEEL_TAGS)
                    and "cp311" in w["url"]}
    raise SystemExit("uv.lock has no onnxruntime entry — cannot build the cpu export")


def cpu_onnxruntime_swap(text: str, lock: Path = ROOT / "uv.lock") -> str:
    """Drop the onnxruntime-gpu block; make the plain onnxruntime block
    unconditional. Refuses (SystemExit) rather than emit a broken file."""
    blocks = _blocks(text)
    gpu = [b for b in blocks if _name(b) == "onnxruntime-gpu"]
    cpu = [b for b in blocks if _name(b) == "onnxruntime"]
    if len(gpu) != 1 or len(cpu) != 1:
        raise SystemExit(f"cpu export: expected one onnxruntime and one "
                         f"onnxruntime-gpu block, got {len(cpu)}/{len(gpu)}")
    ort = cpu[0]
    head = ort[0]
    m = re.match(r"(onnxruntime==[^\s;]+)\s*(;[^\\]*)?(\\?)\s*$", head)
    if not m:
        raise SystemExit(f"cpu export: unexpected onnxruntime line {head!r}")
    ort[0] = m.group(1) + (" \\" if m.group(3) else "")
    hashed = any("--hash=" in l for l in ort)
    if hashed:
        have = {h for l in ort for h in re.findall(r"--hash=sha256:([0-9a-f]+)", l)}
        missing = _locked_ort_hashes(lock) - have
        if missing:
            raise SystemExit(f"cpu export: onnxruntime block lacks x64 wheel "
                             f"hashes {sorted(missing)}")
    kept = [b for b in blocks if b is not gpu[0]]
    return "\n".join(l for b in kept for l in b) + "\n"


def export(extra: str, out: Path, *, hashes: bool = True) -> Path:
    cmd = ["uv", "export", "--frozen", "--no-dev", "--extra", extra]
    if not hashes:
        cmd.append("--no-hashes")
    text = subprocess.run(cmd, cwd=ROOT, check=True, capture_output=True,
                          text=True, encoding="utf-8").stdout
    if extra == "cpu":
        text = cpu_onnxruntime_swap(text)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8", newline="\n")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--extra", required=True, choices=["cpu", "cu124"])
    ap.add_argument("-o", "--output", required=True)
    ap.add_argument("--no-hashes", action="store_true")
    a = ap.parse_args(argv)
    p = export(a.extra, Path(a.output), hashes=not a.no_hashes)
    print(p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
