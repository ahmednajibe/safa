"""M10 T3: the one shared model downloader — used by setup prefetch and
by ``POST /api/models/{id}/download`` (which runs it in a subprocess so
the API process never loads heavy ML imports).

Guarantees: every downloaded file lands as ``<name>.part`` and is
atomically renamed into place; a killed/cancelled download leaves a
``.part`` that the next attempt resumes; SHA-256 is verified where the
manifest records one; ``refresh_capabilities()`` runs after success so
op cards unlock without a restart.

CLI (subprocess protocol, JSON lines on stdout)::

    python -m backend.core.model_download <model_id>

    {"type":"progress","pct":..,"file":..,"done_bytes":..,"total_bytes":..}
    {"type":"done","model_id":".."}
    {"type":"error","detail":".."}
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import threading
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

from backend.core.models_manifest import MODELS, model_dir

ProgressFn = Callable[[dict], None]
CancelFn = Callable[[], bool]


class DownloadCancelled(Exception):
    pass


class DownloadError(Exception):
    pass


class UnknownModel(DownloadError):
    pass


def _check_cancel(cancel: CancelFn) -> None:
    if cancel is not None and cancel():
        raise DownloadCancelled()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch_file(
    url: str,
    dest: Path,
    *,
    on_progress: ProgressFn | None = None,
    cancel: CancelFn = None,
    sha256: str | None = None,
    timeout: int = 60,
) -> Path:
    """Stream ``url`` → ``dest`` via ``dest.part`` (resume) + atomic rename.

    Resume honours ``Range``/206; a server that ignores Range restarts
    the file. A sha mismatch deletes the partial file so a corrupted
    download never lingers as "done".
    """
    _check_cancel(cancel)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    done = part.stat().st_size if part.is_file() else 0
    req = urllib.request.Request(url)
    if done:
        req.add_header("Range", f"bytes={done}-")
    # Any exception (network, cancel, kill) leaves the .part in place —
    # the next attempt resumes it. Only a sha mismatch deletes it.
    with urllib.request.urlopen(req, timeout=timeout) as r:
        if done and r.status != 206:
            # server won't resume → start over
            part.unlink(missing_ok=True)
            done = 0
        total = int(r.headers.get("Content-Length") or 0) + done
        mode = "ab" if done else "wb"
        with part.open(mode) as fh:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                _check_cancel(cancel)
                fh.write(chunk)
                done += len(chunk)
                if on_progress:
                    on_progress(
                        {
                            "type": "progress",
                            "file": dest.name,
                            "done_bytes": done,
                            "total_bytes": total,
                            "pct": (
                                round(done * 100 / total, 1)
                                if total
                                else None
                            ),
                        }
                    )
    if sha256 is not None and _sha256(part) != sha256:
        part.unlink(missing_ok=True)
        raise DownloadError(f"sha256 mismatch on {dest.name}")
    os.replace(part, dest)  # atomic on the same volume
    return dest


# ---- per-model strategies ----------------------------------------------------


def _emit_progress(
    on_progress: ProgressFn | None, file: str, pct: float | None
) -> None:
    if on_progress:
        on_progress({"type": "progress", "file": file, "pct": pct})


def _download_deepfilternet3(
    on_progress: ProgressFn | None, cancel: CancelFn
) -> None:
    """Pinned GitHub zip → sha-verified → staged extract → atomic move.

    Reproduces exactly what ``init_df()``'s ``maybe_download_model``
    produces (``<cache>/DeepFilterNet3/config.ini`` + ``checkpoints/``)
    without paying the torch import in the downloader process."""
    spec = MODELS["deepfilternet3"]
    mdir = model_dir("deepfilternet3")
    target = mdir / "DeepFilterNet3"
    if (target / "config.ini").is_file() or (target / "checkpoints").is_dir():
        _emit_progress(on_progress, "DeepFilterNet3", 100.0)
        return
    url = (
        "https://github.com/Rikorose/DeepFilterNet/raw/v0.5.6"
        "/models/DeepFilterNet3.zip"
    )
    zpath = fetch_file(
        url,
        mdir / "DeepFilterNet3.zip",
        on_progress=on_progress,
        cancel=cancel,
        sha256=spec.sha256,
    )
    _check_cancel(cancel)
    stage = mdir / ".dfn_extract"
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)
    try:
        with zipfile.ZipFile(zpath) as zf:
            zf.extractall(stage)
        inner = stage / "DeepFilterNet3"
        if not inner.is_dir():
            # unexpected zip layout — flatten whatever came out
            subs = [p for p in stage.iterdir()]
            inner = subs[0] if len(subs) == 1 else stage
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        os.replace(inner, target)  # atomic same-volume move
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    zpath.unlink(missing_ok=True)
    _emit_progress(on_progress, "DeepFilterNet3", 100.0)


_WHISPER_REPOS = {
    "whisper_small": "Systran/faster-whisper-small",
    "whisper_turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
}


def _download_whisper(
    model_id: str, on_progress: ProgressFn | None, cancel: CancelFn
) -> None:
    """Pinned HF snapshot into ``models/whisper`` (the hub cache layout
    faster-whisper expects), then the recorded model.bin sha."""
    from huggingface_hub import HfApi, snapshot_download
    from tqdm import tqdm

    spec = MODELS[model_id]
    repo = _WHISPER_REPOS[model_id]
    cache = model_dir(model_id)
    info = HfApi().model_info(
        repo, revision=spec.revision, files_metadata=True
    )
    total = sum(s.size or 0 for s in info.siblings)
    state = {"done": 0}

    class _Prog(tqdm):
        def update(self, n: float = 1):
            _check_cancel(cancel)
            state["done"] += n
            if on_progress:
                on_progress(
                    {
                        "type": "progress",
                        "file": repo.rsplit("/", 1)[-1],
                        "done_bytes": int(state["done"]),
                        "total_bytes": total,
                        "pct": (
                            round(state["done"] * 100 / total, 1)
                            if total
                            else None
                        ),
                    }
                )
            return super().update(n)

        def close(self):  # don't double-count completed files
            super().close()

    snapshot_download(
        repo_id=repo,
        revision=spec.revision,
        cache_dir=str(cache),
        tqdm_class=_Prog,
    )
    _check_cancel(cancel)
    # Verify the recorded weights hash at the pinned snapshot — the
    # point of pinning.
    if spec.sha256:
        snap = (
            cache
            / f"models--{repo.replace('/', '--')}"
            / "snapshots"
            / spec.revision
            / "model.bin"
        )
        if not snap.is_file() or _sha256(snap) != spec.sha256:
            raise DownloadError(
                f"sha256 mismatch on {model_id} model.bin"
            )


def _download_separator(
    model_id: str, on_progress: ProgressFn | None, cancel: CancelFn
) -> None:
    """Drive the real audio-separator load path — the library knows its
    own URLs; each file fetch is routed through ``fetch_file`` so it gets
    .part resume, atomic rename, cancel and (for the weights file) the
    recorded sha."""
    from backend.media import separator

    model = {"htdemucs": "htdemucs", "kim_vocal_2": "kim_vocal_2"}[model_id]
    spec = MODELS[model_id]
    sep = separator._new_separator(model, "cpu", {"voice"}, 7)

    def _fetch(url: str, output_path: str) -> None:
        out = Path(output_path)
        if out.is_file():
            return
        fetch_file(
            url,
            out,
            on_progress=on_progress,
            cancel=cancel,
            sha256=spec.sha256 if out.name == spec.filename else None,
        )

    sep.download_file_if_not_exists = _fetch
    try:
        sep.load_model(separator.MODEL_FILES[model])
    finally:
        try:
            sep.model_instance = None
        except Exception:
            pass


def download_model(
    model_id: str,
    *,
    on_progress: ProgressFn | None = None,
    cancel: CancelFn = None,
) -> dict:
    """Download ``model_id`` (allow-list = the manifest) and verify it.

    Returns ``{"model_id": id, "present": bool}``. Raises
    ``UnknownModel``/``DownloadCancelled``/``DownloadError`` (or an
    underlying network error as ``DownloadError``-wrapped)."""
    if model_id not in MODELS:
        raise UnknownModel(model_id)
    from backend.core.capabilities import _model_present

    if _model_present(model_id):
        return {"model_id": model_id, "present": True, "already": True}
    try:
        if model_id == "silero_vad":
            pass  # bundled in the wheel — nothing to fetch
        elif model_id == "deepfilternet3":
            _download_deepfilternet3(on_progress, cancel)
        elif model_id in _WHISPER_REPOS:
            _download_whisper(model_id, on_progress, cancel)
        elif model_id in ("htdemucs", "kim_vocal_2"):
            _download_separator(model_id, on_progress, cancel)
        else:  # manifest grew a model without a strategy — fail loudly
            raise DownloadError(f"no download strategy for {model_id}")
    except DownloadCancelled:
        raise
    except DownloadError:
        raise
    except BaseException as exc:
        raise DownloadError(f"{type(exc).__name__}: {exc}") from exc
    present = _model_present(model_id)
    try:
        from backend.core.capabilities import refresh_capabilities

        refresh_capabilities()
    except Exception:
        pass
    if not present:
        raise DownloadError(f"{model_id} still missing after download")
    return {"model_id": model_id, "present": True, "already": False}


# ---- subprocess entry point --------------------------------------------------


def _emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=True) + "\n")
    sys.stdout.flush()


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in MODELS:
        _emit(
            {
                "type": "error",
                "detail": "usage: model_download "
                + "/".join(sorted(MODELS)),
            }
        )
        return 2
    model_id = argv[0]
    from backend.core.models_manifest import configure_model_env

    # The only component allowed online — downloads ARE the point here.
    configure_model_env(offline=False)

    def _report(ev: dict) -> None:
        ev = dict(ev)
        ev.setdefault("type", "progress")
        ev["model_id"] = model_id
        _emit(ev)

    try:
        out = download_model(model_id, on_progress=_report)
    except DownloadCancelled:
        _emit({"type": "cancelled", "model_id": model_id})
        return 3
    except BaseException as exc:
        _emit(
            {
                "type": "error",
                "model_id": model_id,
                "detail": f"{type(exc).__name__}: {exc}"[:400],
            }
        )
        return 1
    _emit({"type": "done", "model_id": model_id, "already": out["already"]})
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
