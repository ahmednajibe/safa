"""M10c §9.4: the GPU portable archive splits into <=1.8 GiB 7z volumes;
assemble-gpu.bat verifies every part against SHA256SUMS.txt, refuses
missing / corrupt / unlisted (mixed-version) parts, checks free space,
then extracts with the bundled 7z.exe.

Tested with a tiny fabricated payload split at -v1k so the whole
lifecycle runs in seconds.
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BAT = ROOT / "packaging" / "windows" / "assemble-gpu.bat"
SEVEN = ROOT / "tools" / "7zip" / "7z.exe"

pytestmark = pytest.mark.skipif(
    not SEVEN.is_file(), reason="tools/7zip/7z.exe missing")


def _make_parts(dst: Path) -> tuple[Path, Path]:
    """Fabricate Safa-<v>-win-x64-gpu-portable.7z.NNN + SHA256SUMS.txt."""
    payload = dst / "payload" / "Safa-9.9.9-win-x64-gpu-portable"
    payload.mkdir(parents=True)
    import os
    (payload / "hello.txt").write_text("safa gpu payload\n" * 200)
    (payload / "bin.dat").write_bytes(os.urandom(6000))  # incompressible
    base = dst / "Safa-9.9.9-win-x64-gpu-portable.7z"
    subprocess.run(
        [str(SEVEN), "a", "-mx=1", "-v1k", str(base), str(payload)],
        check=True, capture_output=True, timeout=60,
        cwd=dst,
    )
    parts = sorted(dst.glob("Safa-9.9.9-win-x64-gpu-portable.7z.*"))
    assert len(parts) >= 2, "payload did not split into volumes"
    sums = dst / "Safa-9.9.9-win-x64-gpu-portable-SHA256SUMS.txt"
    sums.write_text("".join(
        f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n"
        for p in parts))
    for f in ("7z.exe", "7z.dll"):
        shutil.copy(SEVEN.parent / f, dst / f)
    shutil.copy(BAT, dst / "assemble-gpu.bat")
    (dst / "payload").mkdir(exist_ok=True)  # keep stray dir out of glob
    return base, sums


def _run(dst: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["cmd", "/c", str(dst / "assemble-gpu.bat")], cwd=dst,
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=120,
    )


@pytest.mark.skipif(not BAT.is_file(), reason="assemble-gpu.bat missing")
def test_assemble_gpu_happy_path(tmp_path):
    _make_parts(tmp_path)
    r = _run(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (tmp_path / "payload" / "Safa-9.9.9-win-x64-gpu-portable"
            / "hello.txt").is_file()


def test_assemble_gpu_refuses_missing_part(tmp_path):
    base, sums = _make_parts(tmp_path)
    part = sorted(tmp_path.glob("*.7z.*"))[-1]
    part.unlink()
    r = _run(tmp_path)
    assert r.returncode == 2
    assert "MISSING" in r.stdout


def test_assemble_gpu_refuses_corrupt_part(tmp_path):
    _make_parts(tmp_path)
    victim = sorted(tmp_path.glob("*.7z.*"))[0]
    victim.write_bytes(victim.read_bytes()[:-1] + b"X")
    r = _run(tmp_path)
    assert r.returncode == 2
    assert "CORRUPT" in r.stdout


def test_assemble_gpu_refuses_unlisted_part(tmp_path):
    _make_parts(tmp_path)
    # A part from another version sitting next to ours.
    rogue = tmp_path / "Safa-9.9.9-win-x64-gpu-portable.7z.999"
    rogue.write_bytes(b"from another build")
    r = _run(tmp_path)
    assert r.returncode == 2
    assert "UNLISTED" in r.stdout


def test_assemble_gpu_refuses_missing_sums(tmp_path):
    base, sums = _make_parts(tmp_path)
    sums.unlink()
    r = _run(tmp_path)
    assert r.returncode == 2
    assert "SHA256SUMS" in r.stdout
