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
    # NOTE: no manifest-pinned relpaths here (see pinned_file_sums) — a
    # pinned path with fake content is exactly what the import must
    # refuse since R2 #1.
    files = {
        "deepfilternet/DeepFilterNet3/model.bin": b"dfn" * 1000,
        "audio-separator/test-weights.th": b"demucs" * 500,
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
            "audio-separator/test-weights.th",
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
            if i.filename == "audio-separator/test-weights.th":
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
    assert (dst / "audio-separator/test-weights.th").is_file()


# ---- R2 #1: manifest-pinned files are the trust anchor -----------------------


def _evil_pinned_pack(tmp_path: Path, rel: str, payload: bytes) -> Path:
    """A pack whose internal SHA256SUMS.txt honestly describes a forged
    file — self-consistent, exactly like a crafted malicious pack."""
    import hashlib

    z = tmp_path / "evil.zip"
    sha = hashlib.sha256(payload).hexdigest()
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("MANIFEST.json", json.dumps({"kind": model_pack.KIND}))
        f.writestr("SHA256SUMS.txt", f"{sha}  {rel}\n")
        f.writestr(rel, payload)
    return z


def test_pinned_file_sums_covers_every_manifest_weights(tmp_path):
    """Every weights file an engine loads must have a pin — htdemucs,
    kim, both whisper model.bin's, the DFN checkpoint."""
    pins = model_pack.pinned_file_sums()
    assert pins["audio-separator/955717e8-8726e21a.th"]
    assert pins["audio-separator/Kim_Vocal_2.onnx"]
    whisper = [r for r in pins if r.endswith("/model.bin")
               and r.startswith("whisper/")]
    assert len(whisper) == 2  # small + turbo pinned snapshots
    assert any("checkpoints/" in r for r in pins)  # DFN ckpt


def test_import_refuses_tampered_pinned_weights(tmp_path, capsys):
    """A pack that swaps a pinned file and rewrites its own sums to
    match must be refused — nothing may be extracted."""
    dst = tmp_path / "m"
    for rel in model_pack.pinned_file_sums():
        evil = _evil_pinned_pack(tmp_path, rel, b"forged weights")
        assert model_pack.import_pack(evil, models_dir=dst) == 1, rel
        evil.unlink()
        assert not dst.joinpath(*rel.split("/")).exists(), rel
    out = capsys.readouterr().out
    assert "integrity" in out
    assert "السلامة" in out  # bilingual refusal


def test_import_prints_zip_sha256(tmp_path, capsys):
    import hashlib

    _, pack = _build(tmp_path)
    sha = hashlib.sha256(pack.read_bytes()).hexdigest()
    assert model_pack.import_pack(pack, models_dir=tmp_path / "m") == 0
    assert sha in capsys.readouterr().out


def test_import_verifies_zip_against_sibling_sums(tmp_path, capsys):
    """A release download keeps SHA256SUMS.txt next to the zip — the zip
    itself is checked against it when present."""
    import hashlib

    _, pack = _build(tmp_path)
    sha = hashlib.sha256(pack.read_bytes()).hexdigest()
    (pack.parent / "SHA256SUMS.txt").write_text(
        f"{sha}  {pack.name}\n{sha}  other-asset.deb\n")
    assert model_pack.import_pack(pack, models_dir=tmp_path / "m") == 0


def test_import_refuses_zip_sums_mismatch(tmp_path, capsys):
    _, pack = _build(tmp_path)
    (pack.parent / "SHA256SUMS.txt").write_text(
        f"{'0' * 64}  {pack.name}\n")
    assert model_pack.import_pack(pack, models_dir=tmp_path / "m") == 1
    assert "SHA256SUMS" in capsys.readouterr().out


def test_import_accepts_genuine_pinned_file(tmp_path):
    """A pack carrying the real pinned files imports cleanly."""
    import hashlib

    src = ROOT / "models"
    rels = [
        "deepfilternet/DeepFilterNet3/config.ini",
        "deepfilternet/DeepFilterNet3/checkpoints/model_120.ckpt.best",
    ]
    if not all((src / r).is_file() for r in rels):
        pytest.skip("models/ not downloaded in this checkout")
    z = tmp_path / "genuine.zip"
    sums = "".join(
        f"{hashlib.sha256((src / r).read_bytes()).hexdigest()}  {r}\n"
        for r in rels)
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("MANIFEST.json", json.dumps({"kind": model_pack.KIND}))
        f.writestr("SHA256SUMS.txt", sums)
        for r in rels:
            f.write(src / r, r)
    dst = tmp_path / "dst"
    assert model_pack.import_pack(z, models_dir=dst) == 0
    assert (dst / rels[0]).is_file()


def test_named_model_with_no_files_refuses(tmp_path):
    # CI once had no models/ tree: an empty "offline" pack must fail loudly
    from backend.core.models_manifest import MODELS

    name = next(k for k, v in MODELS.items() if v.source != "bundled")
    empty = tmp_path / "models empty"
    empty.mkdir()
    with pytest.raises(FileNotFoundError):
        model_pack.stage_pack(tmp_path / "stage", empty, [name])
    with pytest.raises(FileNotFoundError):
        model_pack.build_pack(tmp_path / "p.zip", models_dir=empty,
                              names=[name])


def test_bundled_model_with_no_files_is_fine(tmp_path):
    from backend.core.models_manifest import MODELS

    bundled = [k for k, v in MODELS.items() if v.source == "bundled"]
    if not bundled:
        pytest.skip("no bundled models in manifest")
    empty = tmp_path / "m"
    empty.mkdir()
    assert model_pack.stage_pack(tmp_path / "s", empty, bundled)["files"] == 0


def _hf_cache(models: Path, *, symlink: bool) -> tuple[Path, bytes]:
    """whisper_small's HF cache: blobs/<sha> + snapshots/<rev>/model.bin
    (a symlink to the blob, or a plain copy when symlinks are off)."""
    from backend.core.models_manifest import MODELS

    spec = MODELS["whisper_small"]
    repo = models / spec.cache_subdir / model_pack._hf_subdir(spec.source)
    data = b"weights" * 1000
    blob = repo / "blobs" / "abc123"
    blob.parent.mkdir(parents=True)
    blob.write_bytes(data)
    snap = repo / "snapshots" / "rev1" / "model.bin"
    snap.parent.mkdir(parents=True)
    if symlink:
        os.symlink(blob, snap)
    else:
        snap.write_bytes(data)
    (repo / "refs").mkdir()
    (repo / "refs" / "main").write_text("rev1")
    return repo, data


@pytest.mark.parametrize("symlink", [False, True])
def test_hf_blobs_are_not_packed_twice(tmp_path, symlink):
    # Release run on 16cf73b: GitHub's runner keeps HF weights in blobs/
    # with symlinked snapshots/ — packing both doubled the extra pack to
    # 2915 MB, over the 2 GiB asset cap.
    models = tmp_path / "models dir"
    try:
        repo, data = _hf_cache(models, symlink=symlink)
    except OSError:
        pytest.skip("no symlink privilege on this machine")

    files = model_pack.model_files("whisper_small", models)
    rels = {f.relative_to(repo).as_posix() for f in files}
    assert rels == {"snapshots/rev1/model.bin", "refs/main"}

    out = tmp_path / "p.zip"
    man = model_pack.build_pack(out, models_dir=models, names=["whisper_small"])
    assert man["bytes"] == len(data) + len("rev1")
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
        assert not any("/blobs/" in n for n in names)
        snap = next(n for n in names if n.endswith("snapshots/rev1/model.bin"))
        assert z.read(snap) == data  # symlink followed: real bytes

    stage = tmp_path / "stage"
    model_pack.stage_pack(stage, models, ["whisper_small"])
    staged = next(stage.rglob("model.bin"))
    assert not staged.is_symlink() and staged.read_bytes() == data
    assert not any(p.name == "blobs" for p in stage.rglob("*"))

    # whole-tree pack (no names) skips blobs too
    man_all = model_pack.build_pack(tmp_path / "all.zip", models_dir=models)
    assert man_all["files"] == 2
