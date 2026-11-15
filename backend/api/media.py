"""/api/media — upload, probe, peaks, playback, original file, delete.

Upload flow (M3b Task 0): ``POST /api/media`` takes a raw
``application/octet-stream`` body (never spooled to the system temp dir by
multipart parsing) with the original filename percent-encoded in
``X-Filename`` → ext check → disk check (Content-Length) → stream to
``original.<ext>`` → probe → audio check → DB row → background prep thread
(``source.wav``, ``peaks.json``, ``playback.m4a``, LUFS). Blocking work runs
in the threadpool; a failed prep surfaces as ``ready.error`` /
``prep_failed`` instead of an endless 202.
"""
from __future__ import annotations

import json
import mimetypes
import os
import re
import shutil
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect

from backend.api.errors import err
from backend.core.messages import MESSAGES
from backend.db import connect
from backend.media.decode import decode_to_wav
from backend.media.encode import make_playback_copy
from backend.media.loudness import integrated_lufs
from backend.media.peaks import compute_peaks
from backend.media.probe import ProbeError, probe
from backend.pipeline.types import CancelledError
from backend.media.storage import (
    ALLOWED_EXTS,
    display_name,
    free_space_bytes,
    media_dir,
    new_id,
)

router = APIRouter()

_HEADROOM = 512 << 20  # keep at least 512 MiB free after upload + outputs
_STREAM_CHUNK = 256 << 10
_SPACE_CHECK_EVERY = 256 << 20  # re-check free space per 256 MiB (R1 A2)

_ID_RE = re.compile(r"[0-9a-f]{32}")


def _bad_id(media_id: str) -> bool:
    """R1 A3: only lowercase hex ids may reach filesystem paths."""
    return _ID_RE.fullmatch(media_id) is None


# Per-(media, stream) peaks locks (R1 B11): concurrent on-demand decodes of
# the same stream can't corrupt each other's wav / double-ffmpeg.
_PEAKS_LOCKS_GUARD = threading.Lock()
_PEAKS_LOCKS: dict[tuple[str, int], threading.Lock] = {}


def _peaks_lock(media_id: str, stream: int) -> threading.Lock:
    with _PEAKS_LOCKS_GUARD:
        return _PEAKS_LOCKS.setdefault((media_id, stream), threading.Lock())

_MIME = {
    ".m4a": "audio/mp4", ".mp4": "video/mp4", ".aac": "audio/aac",
    ".wav": "audio/wav", ".mp3": "audio/mpeg", ".flac": "audio/flac",
    ".ogg": "audio/ogg", ".opus": "audio/opus", ".wma": "audio/x-ms-wma",
    ".mkv": "video/x-matroska", ".mov": "video/quicktime",
    ".webm": "video/webm", ".avi": "video/x-msvideo",
}

_MSG = MESSAGES  # SPEC §11: single source in backend/core/messages.py


def _media_type(path: Path) -> str:
    """Content-Type for a stored media/output file."""
    return _MIME.get(
        path.suffix.lower()
    ) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def _jsonable_probe(row) -> dict:
    try:
        return json.loads(row["probe_json"])
    except (json.JSONDecodeError, TypeError):
        return {}


def _media_payload(row, mdir: Path) -> dict:
    p = _jsonable_probe(row)
    return {
        "media_id": row["id"],
        "filename": row["filename"],
        "size_bytes": row["size_bytes"],
        "probe": {k: v for k, v in p.items() if k != "lufs"},
        "lufs": p.get("lufs"),
        "ready": {
            "peaks": (mdir / "peaks.json").is_file(),
            "playback": (mdir / "playback.m4a").is_file(),
            "error": p.get("prep_error"),
        },
    }


def _row(db_path: Path, media_id: str):
    con = connect(db_path)
    try:
        return con.execute(
            "SELECT * FROM media WHERE id = ?", (media_id,)
        ).fetchone()
    finally:
        con.close()


def _patch_probe(db_path: Path, media_id: str, **fields) -> None:
    """Merge ``fields`` into the media row's ``probe_json``."""
    con = connect(db_path)
    try:
        row = con.execute(
            "SELECT probe_json FROM media WHERE id = ?", (media_id,)
        ).fetchone()
        if row:
            p = _jsonable_probe(row)
            p.update(fields)
            con.execute(
                "UPDATE media SET probe_json = ? WHERE id = ?",
                (json.dumps(p), media_id),
            )
            con.commit()
    finally:
        con.close()


# Per-media prep cancellation registry (R1 B1): DELETE signals the event,
# the prep thread checks between steps and decode_to_wav kills its ffmpeg.
_PREP_LOCK = threading.Lock()
_PREP_CANCEL: dict[str, threading.Event] = {}
_PREP_THREADS: dict[str, threading.Thread] = {}


def _prepare(
    db_path: Path,
    mdir: Path,
    media_id: str,
    stream_index: int,
    cancel: threading.Event | None = None,
) -> None:
    """Background prep: working wav, peaks, browser copy, loudness.

    On failure the traceback goes to ``prep.log`` and a short message lands
    in ``probe_json["prep_error"]`` so the UI can show it instead of waiting
    on peaks that will never exist.
    """

    def _gone() -> bool:
        # The media was deleted under us — never write into its dir again.
        return (cancel is not None and cancel.is_set()) or not mdir.is_dir()

    try:
        originals = list(mdir.glob("original.*"))
        if not originals or _gone():
            return
        source = mdir / "source.wav"
        decode_to_wav(
            originals[0], source, stream_index, should_cancel=_gone
        )
        if _gone():
            return
        make_playback_copy(source, mdir / "playback.m4a")
        if _gone():
            return
        lufs = integrated_lufs(source)
        if _gone():
            return
        _patch_probe(db_path, media_id, lufs=lufs)
        if _gone():
            return
        # peaks.json is written last: it is the "prep done" marker.
        (mdir / "peaks.json").write_text(
            json.dumps(compute_peaks(source)), encoding="utf-8"
        )
    except Exception as exc:
        if _gone() or isinstance(exc, CancelledError):
            return  # deleted mid-prep — stay silent
        try:
            (mdir / "prep.log").write_text(
                traceback.format_exc(), encoding="utf-8"
            )
        except OSError:
            pass
        _patch_probe(db_path, media_id, prep_error=str(exc)[:300])
    finally:
        with _PREP_LOCK:
            _PREP_CANCEL.pop(media_id, None)
            _PREP_THREADS.pop(media_id, None)


def _insufficient(needed: int, free: int) -> JSONResponse:
    needed_gb = round(needed / 1e9, 1)
    free_gb = round(free / 1e9, 1)
    return err(
        507, "insufficient_storage",
        MESSAGES["insufficient_storage"].format(
            needed_gb=needed_gb, free_gb=free_gb
        ),
        params={"needed_gb": needed_gb, "free_gb": free_gb},
    )


def _content_length(request: Request) -> int | None:
    raw = request.headers.get("content-length")
    try:
        return int(raw) if raw is not None else None
    except ValueError:
        return None


def _insert_media_row(
    db_path: Path,
    media_id: str,
    filename: str,
    original: Path,
    size_bytes: int,
    p,
) -> None:
    con = connect(db_path)
    try:
        con.execute(
            "INSERT INTO media (id, filename, stored_path, size_bytes,"
            " probe_json, created_at) VALUES (?,?,?,?,?,?)",
            (
                media_id,
                filename,
                str(original),
                size_bytes,
                json.dumps(p.to_dict()),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        con.commit()
    finally:
        con.close()


@router.post("/api/media", status_code=201)
async def upload_media(request: Request):
    settings = request.app.state.settings
    filename = unquote(request.headers.get("x-filename", "upload"))
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_EXTS:
        return err(415, "unsupported_type", _MSG["unsupported_type"])

    content_length = _content_length(request)
    if content_length is not None:
        needed = content_length * 4 + _HEADROOM
        free = await run_in_threadpool(free_space_bytes, settings.data_dir)
        if free < needed:
            return _insufficient(needed, free)

    media_id = new_id()
    mdir = media_dir(media_id, settings.data_dir)
    mdir.mkdir(parents=True, exist_ok=True)
    original = mdir / f"original.{ext}"
    limit = settings.max_upload_mb << 20
    try:
        written = 0
        next_space_check = _SPACE_CHECK_EVERY
        with original.open("wb") as fh:
            async for chunk in request.stream():
                written += len(chunk)
                # R1 A2: a lying Content-Length must not skip the checks —
                # fail over it and re-verify disk space every 256 MiB.
                if content_length is not None and written > content_length:
                    fh.close()
                    raise _TooLarge()
                if written > limit:
                    fh.close()
                    raise _TooLarge()
                if written >= next_space_check:
                    free = await run_in_threadpool(
                        free_space_bytes, settings.data_dir
                    )
                    if free < _HEADROOM:
                        fh.close()
                        raise _NoSpace(free)
                    next_space_check = written + _SPACE_CHECK_EVERY
                fh.write(chunk)

        if content_length is None:  # no early check possible → verify now
            needed = written * 4 + _HEADROOM
            free = await run_in_threadpool(
                free_space_bytes, settings.data_dir
            )
            if free < needed:
                shutil.rmtree(mdir, ignore_errors=True)
                return _insufficient(needed, free)
        try:
            p = await run_in_threadpool(probe, original)
        except ProbeError:
            shutil.rmtree(mdir, ignore_errors=True)
            return err(422, "unreadable", _MSG["unreadable"])
        if not p.audio:
            shutil.rmtree(mdir, ignore_errors=True)
            return err(422, "no_audio", _MSG["no_audio"])

        await run_in_threadpool(
            _insert_media_row,
            settings.db_path,
            media_id,
            display_name(filename),
            original,
            written,
            p,
        )
    except _TooLarge:
        shutil.rmtree(mdir, ignore_errors=True)
        return err(413, "too_large", _MSG["too_large"])
    except _NoSpace as exc:
        shutil.rmtree(mdir, ignore_errors=True)
        return _insufficient(_HEADROOM, exc.free)
    except ClientDisconnect:
        shutil.rmtree(mdir, ignore_errors=True)
        return err(400, "client_disconnected", _MSG["client_disconnected"])
    except Exception:
        shutil.rmtree(mdir, ignore_errors=True)
        raise

    cancel = threading.Event()
    prep = threading.Thread(
        target=_prepare,
        args=(
            settings.db_path, mdir, media_id, p.audio[0].index, cancel
        ),
        daemon=True,
    )
    with _PREP_LOCK:
        _PREP_CANCEL[media_id] = cancel
        _PREP_THREADS[media_id] = prep
    prep.start()
    return JSONResponse(
        {
            "media_id": media_id,
            "filename": display_name(filename),
            "size_bytes": written,
            "probe": p.to_dict(),
            "lufs": None,
        },
        status_code=201,
    )


class _TooLarge(Exception):
    pass


class _NoSpace(Exception):
    def __init__(self, free: int):
        super().__init__("disk filled during upload")
        self.free = free


@router.get("/api/media/{media_id}")
def get_media(request: Request, media_id: str):
    settings = request.app.state.settings
    if _bad_id(media_id):
        return err(404, "media_not_found", _MSG["media_not_found"])
    row = _row(settings.db_path, media_id)
    if row is None:
        return err(404, "media_not_found", _MSG["media_not_found"])
    return _media_payload(row, media_dir(media_id, settings.data_dir))


@router.get("/api/media/{media_id}/peaks")
def get_peaks(request: Request, media_id: str, stream: int | None = None):
    settings = request.app.state.settings
    if _bad_id(media_id):
        return err(404, "media_not_found", _MSG["media_not_found"])
    row = _row(settings.db_path, media_id)
    if row is None:
        return err(404, "media_not_found", _MSG["media_not_found"])
    mdir = media_dir(media_id, settings.data_dir)
    p = _jsonable_probe(row)
    audio = p.get("audio") or []
    default_index = audio[0]["index"] if audio else None

    if stream is None or stream == default_index:
        peaks_path = mdir / "peaks.json"
        if not peaks_path.is_file():
            if p.get("prep_error"):
                return err(500, "prep_failed", _MSG["prep_failed"])
            return err(202, "processing", _MSG["processing"])
        return JSONResponse(json.loads(peaks_path.read_text(encoding="utf-8")))

    if stream not in {a["index"] for a in audio}:
        return err(422, "no_such_stream", _MSG["no_such_stream"])
    peaks_path = mdir / f"peaks_{stream}.json"
    if not peaks_path.is_file():
        # R1 B11: one decoder per (media, stream); peaks go to a temp file
        # then atomically replace the target so readers never see a torn
        # JSON; the multi-GB stream wav is removed in all cases.
        with _peaks_lock(media_id, stream):
            if not peaks_path.is_file():
                originals = list(mdir.glob("original.*"))
                if not originals:
                    return err(
                        404, "media_not_found", _MSG["media_not_found"]
                    )
                stream_wav = mdir / f"stream_{stream}.wav"
                tmp = peaks_path.with_name(peaks_path.name + ".tmp")
                try:
                    decode_to_wav(originals[0], stream_wav, stream)
                    tmp.write_text(
                        json.dumps(compute_peaks(stream_wav)),
                        encoding="utf-8",
                    )
                    os.replace(tmp, peaks_path)
                finally:
                    stream_wav.unlink(missing_ok=True)
                    tmp.unlink(missing_ok=True)
    return JSONResponse(json.loads(peaks_path.read_text(encoding="utf-8")))


@router.get("/api/media/{media_id}/playback")
def get_playback(request: Request, media_id: str):
    settings = request.app.state.settings
    if _bad_id(media_id):
        return err(404, "media_not_found", _MSG["media_not_found"])
    row = _row(settings.db_path, media_id)
    if row is None:
        return err(404, "media_not_found", _MSG["media_not_found"])
    playback = media_dir(media_id, settings.data_dir) / "playback.m4a"
    if not playback.is_file():
        if _jsonable_probe(row).get("prep_error"):
            return err(500, "prep_failed", _MSG["prep_failed"])
        return err(202, "processing", _MSG["processing"])
    return _file_response(playback, request, "audio/mp4")


@router.get("/api/media/{media_id}/file")
def get_file(request: Request, media_id: str):
    settings = request.app.state.settings
    if _bad_id(media_id):
        return err(404, "media_not_found", _MSG["media_not_found"])
    row = _row(settings.db_path, media_id)
    if row is None:
        return err(404, "media_not_found", _MSG["media_not_found"])
    path = Path(row["stored_path"])
    if not path.is_file():
        return err(404, "media_not_found", _MSG["media_not_found"])
    return _file_response(path, request, _media_type(path))


@router.delete("/api/media/{media_id}", status_code=204)
def delete_media(request: Request, media_id: str):
    settings = request.app.state.settings
    if _bad_id(media_id):
        return err(404, "media_not_found", _MSG["media_not_found"])
    # R1 B1: stop an in-flight prep before deleting — decode_to_wav kills
    # its ffmpeg and the _gone checks stop further writes into mdir.
    with _PREP_LOCK:
        cancel = _PREP_CANCEL.get(media_id)
    if cancel is not None:
        cancel.set()
    con = connect(settings.db_path)
    job_ids: list[str] = []
    try:
        row = con.execute(
            "SELECT id FROM media WHERE id = ?", (media_id,)
        ).fetchone()
        if row is None:
            return err(404, "media_not_found", _MSG["media_not_found"])
        busy = con.execute(
            "SELECT COUNT(*) FROM jobs WHERE media_id = ?"
            " AND state IN ('queued','running')",
            (media_id,),
        ).fetchone()[0]
        if busy:
            return err(409, "in_use", _MSG["in_use"])
        job_ids = [
            r[0]
            for r in con.execute(
                "SELECT id FROM jobs WHERE media_id = ?", (media_id,)
            ).fetchall()
        ]
        # Terminal job history goes with the media — one transaction so an
        # FK violation leaves every row intact (R1 B1).
        con.execute(
            "DELETE FROM job_files WHERE job_id IN "
            "(SELECT id FROM jobs WHERE media_id = ?)",
            (media_id,),
        )
        con.execute("DELETE FROM jobs WHERE media_id = ?", (media_id,))
        con.execute("DELETE FROM media WHERE id = ?", (media_id,))
        con.commit()
    finally:
        con.close()
    # Give the prep thread a moment to notice the cancel before rmtree so an
    # open ffmpeg output can't leave stray files on Windows.
    with _PREP_LOCK:
        prep = _PREP_THREADS.get(media_id)
    if prep is not None and prep.is_alive():
        prep.join(timeout=5.0)
    shutil.rmtree(media_dir(media_id, settings.data_dir), ignore_errors=True)
    for jid in job_ids:
        shutil.rmtree(
            Path(settings.data_dir) / "jobs" / jid, ignore_errors=True
        )
    return Response(status_code=204)


# ---- HTTP Range (Review Focus 5: browser video seek) -------------------------

_RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)$")


def _slice_iter(path: Path, start: int, length: int):
    with path.open("rb") as fh:
        fh.seek(start)
        left = length
        while left > 0:
            chunk = fh.read(min(_STREAM_CHUNK, left))
            if not chunk:
                break
            left -= len(chunk)
            yield chunk


def _file_response(path: Path, request: Request, media_type: str) -> Response:
    size = path.stat().st_size
    range_header = request.headers.get("range", "")
    m = _RANGE_RE.match(range_header.strip()) if range_header else None
    if not m or not (m.group(1) or m.group(2)):
        return FileResponse(
            path, media_type=media_type,
            headers={"Accept-Ranges": "bytes"},
        )
    start_s, end_s = m.group(1), m.group(2)
    if not start_s:  # suffix range: bytes=-N
        length = min(int(end_s), size)
        start, end = size - length, size - 1
    else:
        start = int(start_s)
        end = min(int(end_s), size - 1) if end_s else size - 1
        length = end - start + 1
    if start >= size or length <= 0:
        return Response(
            status_code=416,
            headers={"Content-Range": f"bytes */{size}"},
        )
    return StreamingResponse(
        _slice_iter(path, start, length),
        status_code=206,
        media_type=media_type,
        headers={
            "Content-Range": f"bytes {start}-{end}/{size}",
            "Content-Length": str(length),
            "Accept-Ranges": "bytes",
        },
    )
