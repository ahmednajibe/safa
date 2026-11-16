"""M10c §9.18: offline model pack — build, import, resume, tamper.

The pack mirrors the ``models/`` tree plus MANIFEST.json + SHA256SUMS.txt.
Import skips already-correct files (resume-safe), stages every file as
``<name>.safa-part`` and replaces only after its hash verifies, refuses
missing/extra/corrupt members, and survives Arabic paths.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from backend.core import model_pack

ROOT = Path(__file__).resolve().parents[1]


def _fake_models(base: Path) -> Path:
    files = {
        "deepfilternet/DeepFilterNet3/model.bin": b"dfn" * 1000,
        "audio-separator/955717e8-8726e21a.th": b"demucs" * 500,
        "whisper/models--x/snapshots/abc/model.bin": b"whisper" * 300,
        "whisper/models--x/snapshots/abc/config.json": b"{}",
    }
    for rel, data in files.items():
        p = base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return base


def _build(tmp_path: Path) -> tuple[Path, Path]:
    src = _fake_models(tmp_path / "models src")
    out = tmp_path / "pack.zip"
    model_pack.build_pack(out, models_dir=src)
    return src, out


def _sum_map(z: zipfile.ZipFile) -> dict[str, str]:
    raw = z.read("SHA256SUMS.txt").decode()
    return {
        line.split()[-1]: line.split()[0]
        for line in raw.splitlines() if line.strip()
    }


def test_build_pack_manifest_and_sums(tmp_path):
    src, out = _build(tmp_path)
    with zipfile.ZipFile(out) as z:
        manifest = json.loads(z.read("MANIFEST.json"))
        assert manifest["kind"] == model_pack.KIND
        assert manifest["files"] == 4
        sums = _sum_map(z)
        assert set(sums) == {
            "deepfilternet/DeepFilterNet3/model.bin",
            "audio-separator/955717e8-8726e21a.th",
            "whisper/models--x/snapshots/abc/model.bin",
            "whisper/models--x/snapshots/abc/config.json",
        }
        for rel in sums:
            assert z.read(rel)  # every listed file is in the zip


def test_import_into_empty_models_dir(tmp_path, capsys):
    _, pack = _build(tmp_path)
    dst = tmp_path / "نماذج"  # Arabic path must work
    rc = model_pack.import_pack(pack, models_dir=dst)
    assert rc == 0
    for rel in (
        "deepfilternet/DeepFilterNet3/model.bin",
        "whisper/models--x/snapshots/abc/model.bin",
    ):
        assert (dst / rel).is_file()


def test_import_resume_skips_existing(tmp_path, capsys):
    src, pack = _build(tmp_path)
    dst = tmp_path / "models"
    assert model_pack.import_pack(pack, models_dir=dst) == 0
    capsys.readouterr()
    # Second run: every file already correct → skipped, still exit 0.
    assert model_pack.import_pack(pack, models_dir=dst) == 0
    out = capsys.readouterr().out
    assert "0 file(s), 4 already present" in out


def test_import_detects_corrupt_target(tmp_path):
    _, pack = _build(tmp_path)
    dst = tmp_path / "models"
    assert model_pack.import_pack(pack, models_dir=dst) == 0
    victim = dst / "deepfilternet/DeepFilterNet3/model.bin"
    victim.write_bytes(b"tampered!")
    assert model_pack.import_pack(pack, models_dir=dst) == 0
    assert victim.read_bytes() == b"dfn" * 1000  # restored from pack


def test_import_refuses_foreign_zip(tmp_path, capsys):
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("random.txt", "x")
    assert model_pack.import_pack(bad, models_dir=tmp_path / "m") == 1
    assert "MANIFEST.json missing" in capsys.readouterr().out


def test_import_refuses_extra_member(tmp_path, capsys):
    _, pack = _build(tmp_path)
    patched = tmp_path / "patched.zip"
    with zipfile.ZipFile(pack) as zin, \
            zipfile.ZipFile(patched, "w") as zout:
        for i in zin.infolist():
            zout.writestr(i, zin.read(i.filename))
        zout.writestr("evil/extra.bin", b"payload")
    assert model_pack.import_pack(patched, models_dir=tmp_path / "m") == 1
    assert "unlisted files" in capsys.readouterr().out


def test_import_refuses_missing_member(tmp_path, capsys):
    _, pack = _build(tmp_path)
    patched = tmp_path / "patched.zip"
    with zipfile.ZipFile(pack) as zin, \
            zipfile.ZipFile(patched, "w") as zout:
        for i in zin.infolist():
            if i.filename == "audio-separator/955717e8-8726e21a.th":
                continue  # drop a listed file
            zout.writestr(i, zin.read(i.filename))
    assert model_pack.import_pack(patched, models_dir=tmp_path / "m") == 1
    assert "incomplete" in capsys.readouterr().out


def test_import_refuses_traversal(tmp_path, capsys):
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as z:
        z.writestr("MANIFEST.json", json.dumps({"kind": model_pack.KIND}))
        z.writestr("SHA256SUMS.txt",
                   "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855  ../escape.bin\n")
        z.writestr("../escape.bin", b"")
    assert model_pack.import_pack(evil, models_dir=tmp_path / "m") == 1
    assert not (tmp_path / "escape.bin").exists()


def test_models_import_cli(tmp_path):
    _, pack = _build(tmp_path)
    dst = tmp_path / "cli models"
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    r = subprocess.run(
        [
            sys.executable, "-m", "backend.launcher",
            "models", "import", str(pack), "--models-dir", str(dst),
        ],
        capture_output=True, text=True, encoding="utf-8",
        cwd=str(ROOT), env=env, timeout=120,
    )
    assert r.returncode == 0, r.stderr[-500:]
    assert "imported 4 file(s)" in r.stdout
    assert (dst / "audio-separator/955717e8-8726e21a.th").is_file()
