"""Offline model packs (M10c §9.18): zip the ``models/`` tree once, carry
it on USB, import it anywhere — no internet needed after that.

Pack layout (paths relative to ``MODELS_DIR``)::

    MANIFEST.json       {"kind": "safa-model-pack", "name", "version",
                         "models": [...], "files", "bytes"}
    SHA256SUMS.txt      "<sha256>  <relpath>" per data file
    deepfilternet/...   model files in their models/ tree positions
    audio-separator/...
    whisper/...

``import_pack()`` is resume-safe: files already present with a matching
hash are skipped; files extract to ``<target>.safa-part`` and are
``os.replace``d only after their hash verifies — a re-run never
re-downloads and never leaves a half file at the target name.
"""
from __future__ import annotations

import hashlib
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from backend.core.paths import MODELS_DIR as _DEFAULT_MODELS_DIR
from backend.core.release_info import APP_NAME, VERSION

KIND = "safa-model-pack"
_PART_SUFFIX = ".safa-part"


def _sha256_file(path: Path, _bufsize: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(_bufsize):
            h.update(chunk)
    return h.hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _iter_pack_files(models_dir: Path) -> list[Path]:
    """Every regular file under ``models_dir``, posix-rel, sorted."""
    out = []
    for p in models_dir.rglob("*"):
        if p.is_file() and not p.name.endswith(_PART_SUFFIX):
            out.append(p)
    return sorted(out, key=lambda p: p.as_posix())


def _hf_subdir(source: str) -> str | None:
    """``huggingface:Systran/faster-whisper-small`` → ``models--Systran--faster-whisper-small``."""
    if not source.startswith("huggingface:"):
        return None
    repo = source.split(":", 1)[1].split()[0]
    return "models--" + repo.replace("/", "--")


# deepfilternet3's spec.sha256 pins the *release zip*, not the extracted
# files — pin the torch.load'd checkpoint + config of the pinned release
# (v0.5.6) directly, measured from the genuine extracted tree.
_EXTRA_PINS = {
    "deepfilternet/DeepFilterNet3/checkpoints/model_120.ckpt.best":
        "23b92884f63ccf54bb026014604625ab231657b6480df65db4095c4c171e6003",
    "deepfilternet/DeepFilterNet3/config.ini":
        "415eb925d44990d938fb739f514aa3662c1ec0ea836cff044fa1291b82cb4290",
}


def pinned_file_sums() -> dict[str, str]:
    """Pack-relpath → expected sha256 for every weights file the
    manifest pins. ``SHA256SUMS.txt`` inside a pack is self-authored —
    a tampered pack rewrites it to match — so these are the trust
    anchor for import (R2 #1)."""
    from backend.core.models_manifest import MODELS

    pins: dict[str, str] = {}
    for spec in MODELS.values():
        if spec.sha256 is None:
            continue
        if spec.filename:
            pins[f"{spec.cache_subdir}/{spec.filename}"] = spec.sha256
        elif spec.revision:
            hf = _hf_subdir(spec.source)
            if hf is not None:
                # manifest sha = model.bin at the pinned snapshot
                pins[f"{spec.cache_subdir}/{hf}/snapshots/"
                     f"{spec.revision}/model.bin"] = spec.sha256
    pins.update(_EXTRA_PINS)
    return pins


def _verify_external_sums(zip_path: Path, zip_sha: str, out) -> bool:
    """A release download keeps ``SHA256SUMS.txt`` next to the zip —
    verify the zip itself against it when present (the pack's internal
    sums are self-authored). Refuses a listed-but-mismatched zip."""
    sums_file = zip_path.with_name("SHA256SUMS.txt")
    if not sums_file.is_file():
        return True
    try:
        lines = sums_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return True
    for line in lines:
        parts = line.split()
        if len(parts) < 2:
            continue
        if Path(parts[-1].lstrip("*")).name == zip_path.name:
            if parts[0].lower() == zip_sha:
                return True
            out(f"SHA256SUMS.txt mismatch — listed {parts[0].lower()}, "
                f"zip is {zip_sha} — refusing (tampered download?)")
            out("ملف SHA256SUMS.txt المجاور لا يطابق بصمة الحزمة — "
                "مرفوض (تنزيل معدّل؟)")
            return False
    out(f"note: SHA256SUMS.txt does not list {zip_path.name} — "
        "skipping zip-level check")
    return True


def model_files(name: str, models_dir: Path) -> list[Path]:
    """Files belonging to one manifest model — finer than a whole
    ``cache_subdir`` (whisper/ holds both small and turbo)."""
    from backend.core.models_manifest import MODELS

    spec = MODELS[name]
    base = Path(models_dir) / spec.cache_subdir
    if spec.source == "bundled":
        return []  # ships inside its wheel — nothing to pack
    if spec.filename:
        f = base / spec.filename
        return [f] if f.is_file() else []
    hf = _hf_subdir(spec.source)
    if hf is not None:
        sub = base / hf
        return _iter_pack_files(sub) if sub.is_dir() else []
    return _iter_pack_files(base) if base.is_dir() else []


def stage_pack(out_dir: Path, models_dir: Path, names: list[str]) -> dict:
    """Copy one pack's model files into ``out_dir`` (models/ tree layout)
    — the payload shape offline installers bundle. Returns a summary."""
    import shutil

    src = Path(models_dir)
    out_dir = Path(out_dir)
    files: list[Path] = []
    for n in names:
        files += model_files(n, src)
    files = sorted(set(files), key=lambda p: p.as_posix())
    for f in files:
        rel = f.relative_to(src)
        dst = out_dir.joinpath(*rel.parts)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, dst)
    return {"models": names, "files": len(files),
            "bytes": sum(f.stat().st_size for f in files)}


def build_pack(
    out_zip: Path,
    models_dir: Path | None = None,
    names: list[str] | None = None,
) -> dict:
    """Zip the model trees for ``names`` (defaults: every subdir of
    ``models_dir`` that is not an empty cache dir) plus the checksum
    manifest. Returns the manifest dict."""
    src = Path(models_dir) if models_dir else Path(_DEFAULT_MODELS_DIR)
    if names:
        files: list[Path] = []
        for n in names:
            files += model_files(n, src)
    else:
        files = [p for d in src.iterdir() if d.is_dir()
                 for p in _iter_pack_files(d)]
    files = sorted(set(files), key=lambda p: p.as_posix())

    sums = {p.relative_to(src).as_posix(): _sha256_file(p) for p in files}
    manifest = {
        "kind": KIND,
        "name": f"{APP_NAME} models",
        "version": VERSION,
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "models": names or sorted({p.relative_to(src).parts[0] for p in files}),
        "files": len(files),
        "bytes": sum(p.stat().st_size for p in files),
    }
    out_zip = Path(out_zip)
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_zip.with_suffix(out_zip.suffix + ".part")
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED,
                             compresslevel=1) as z:
            z.writestr("MANIFEST.json",
                       json.dumps(manifest, indent=2, ensure_ascii=False))
            z.writestr(
                "SHA256SUMS.txt",
                "".join(f"{s}  {r}\n" for r, s in sorted(sums.items())),
            )
            for p in files:
                z.write(p, p.relative_to(src).as_posix())
        tmp.replace(out_zip)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return manifest


def _read_sums(z: zipfile.ZipFile) -> dict[str, str]:
    try:
        raw = z.read("SHA256SUMS.txt").decode("utf-8")
    except KeyError:
        raise ValueError("SHA256SUMS.txt missing — not a Safa model pack")
    sums: dict[str, str] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        sha, _, rel = line.partition(" ")
        rel = rel.strip().lstrip("*").strip()
        sums[rel] = sha.strip().lower()
    return sums


def _safe_rel(rel: str) -> PurePosixPath:
    """Reject absolute paths and traversal from the archive."""
    p = PurePosixPath(rel)
    if p.is_absolute() or ".." in p.parts or "\\" in rel or ":" in rel:
        raise ValueError(f"unsafe member path {rel!r}")
    return p


def import_pack(
    zip_path: Path,
    models_dir: Path | None = None,
    out=print,
) -> int:
    """Import an offline model pack. Returns 0 on success, 1 on a
    corrupt/foreign pack or failed verification. Never writes a target
    until its hash verifies (``<name>.safa-part`` staging + replace)."""
    zip_path = Path(zip_path)
    dst_root = Path(models_dir) if models_dir else Path(_DEFAULT_MODELS_DIR)
    if not zip_path.is_file():
        out(f"pack not found: {zip_path}")
        return 1
    zip_sha = _sha256_file(zip_path)
    out(f"pack sha256: {zip_sha}")
    if not _verify_external_sums(zip_path, zip_sha, out):
        return 1
    try:
        z = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile:
        out(f"not a zip file: {zip_path}")
        return 1
    with z:
        try:
            manifest = json.loads(z.read("MANIFEST.json").decode("utf-8"))
        except KeyError:
            out("MANIFEST.json missing — not a Safa model pack")
            return 1
        if not isinstance(manifest, dict) or manifest.get("kind") != KIND:
            out(f"foreign zip — expected kind={KIND}")
            return 1
        try:
            sums = _read_sums(z)
        except ValueError as exc:
            out(str(exc))
            return 1
        # R2 #1: the pack's sums are self-authored — every manifest-
        # pinned file it claims must match the manifest hash, checked
        # before a single byte is written.
        pins = pinned_file_sums()
        forged = [r for r, s in sorted(sums.items())
                  if r in pins and pins[r] != s]
        if forged:
            out("integrity check failed — pack file(s) do not match the "
                "pinned manifest hashes (tampered pack?):")
            out("فشل فحص السلامة — ملفات الحزمة لا تطابق البصمات "
                "المثبتة في البيان (حزمة معدّلة؟):")
            for rel in forged[:10]:
                out(f"  {rel}")
            return 1
        listed = set(sums)
        members = {i.filename for i in z.infolist() if not i.is_dir()}
        members -= {"MANIFEST.json", "SHA256SUMS.txt"}
        missing = sorted(listed - members)
        extra = sorted(members - listed)
        if missing:
            out("pack is incomplete — missing files:")
            for m in missing[:10]:
                out(f"  {m}")
            return 1
        if extra:
            out("pack has unlisted files — refusing (tampering?):")
            for m in extra[:10]:
                out(f"  {m}")
            return 1

        errors = 0
        done = 0
        skipped = 0
        for rel, sha in sorted(sums.items()):
            try:
                rel_p = _safe_rel(rel)
            except ValueError as exc:
                out(str(exc))
                return 1
            target = dst_root.joinpath(*rel_p.parts)
            if target.is_file() and _sha256_file(target) == sha:
                skipped += 1
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            part = target.with_name(target.name + _PART_SUFFIX)
            try:
                with z.open(rel) as r, part.open("wb") as w:
                    h = hashlib.sha256()
                    while chunk := r.read(1 << 20):
                        h.update(chunk)
                        w.write(chunk)
                if h.hexdigest() != sha:
                    part.unlink(missing_ok=True)
                    out(f"  hash mismatch: {rel}")
                    errors += 1
                    continue
                part.replace(target)
                done += 1
                out(f"  ok {rel}")
            except Exception as exc:
                part.unlink(missing_ok=True)
                out(f"  failed {rel}: {exc}")
                errors += 1
        if errors:
            out(f"import failed for {errors} file(s) — re-run to resume")
            return 1
        out(f"imported {done} file(s), {skipped} already present "
            f"→ {dst_root}")
        return 0


def _cli(argv: list[str]) -> int:
    """``python -m backend.core.model_pack`` — packaging helper.

      build <zip> [--models-dir D] [--names a,b]   → write a pack zip
      stage <dir> [--models-dir D] --names a,b     → copy files to a dir
    """
    import argparse

    ap = argparse.ArgumentParser(prog="model_pack")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("build", "stage"):
        sp = sub.add_parser(name)
        sp.add_argument("out", type=Path)
        sp.add_argument("--models-dir", type=Path, default=None)
        sp.add_argument("--names", default=None,
                        help="comma-separated manifest model keys")
    args = ap.parse_args(argv)
    names = args.names.split(",") if args.names else None
    if args.cmd == "build":
        manifest = build_pack(args.out, models_dir=args.models_dir,
                              names=names)
        print(json.dumps(manifest, indent=2))
        return 0
    if not names:
        ap.error("stage requires --names")
    summary = stage_pack(args.out, models_dir=args.models_dir or
                         _DEFAULT_MODELS_DIR, names=names)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_cli(sys.argv[1:]))


__all__ = ["KIND", "build_pack", "import_pack", "model_files", "stage_pack"]
