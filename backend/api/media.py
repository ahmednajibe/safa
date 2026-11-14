"""/api/media — upload, probe, peaks, playback, original file, delete.

Upload flow (M3a plan): ext check → disk check → stream to ``original.<ext>``
in 1 MiB chunks → probe → audio check → DB row → background prep thread
(``source.wav``, ``peaks.json``, ``playback.m4a``, LUFS).
"""
from __future__ import annotations

import json
import mimetypes
import re
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

from backend.api.errors import err
from backend.db import connect
from backend.media.decode import decode_to_wav
from backend.media.encode import make_playback_copy
from backend.media.loudness import integrated_lufs
from backend.media.peaks import compute_peaks
from backend.media.probe import ProbeError, probe
from backend.media.storage import (
    ALLOWED_EXTS,
    display_name,
    free_space_bytes,
    media_dir,
    new_id,
)

router = APIRouter()

_CHUNK = 1 << 20  # 1 MiB upload streaming chunks
_HEADROOM = 512 << 20  # keep at least 512 MiB free after upload + outputs
_STREAM_CHUNK = 256 << 10

_MIME = {
    ".m4a": "audio/mp4", ".mp4": "video/mp4", ".aac": "audio/aac",
    ".wav": "audio/wav", ".mp3": "audio/mpeg", ".flac": "audio/flac",
    ".ogg": "audio/ogg", ".opus": "audio/opus", ".wma": "audio/x-ms-wma",
    ".mkv": "video/x-matroska", ".mov": "video/quicktime",
    ".webm": "video/webm", ".avi": "video/x-msvideo",
}

_MSG = {
    "unsupported_type": "This file type isn't supported. Try WAV, MP3, MP4\u2026",
    "no_audio": "This video has no audio track. Choose another file.",
    "unreadable": "We couldn't read this file. It may be damaged or not a media file.",
    "too_large": "This file is too large.",
    "media_not_found": "This file was deleted or never existed.",
    "in_use": "A running job is using this file.",
    "no_such_stream": "This file has no audio track with that number.",
}


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


def _prepare(db_path: Path, mdir: Path, media_id: str, stream_index: int) -> None:
    """Background prep: working wav, peaks, browser copy, loudness."""
    try:
        originals = list(mdir.glob("original.*"))
        if not originals:
            return
        source = mdir / "source.wav"
        decode_to_wav(originals[0], source, stream_index)
        make_playback_copy(source, mdir / "playback.m4a")
        lufs = integrated_lufs(source)
        con = connect(db_path)
        try:
            row = con.execute(
                "SELECT probe_json FROM media WHERE id = ?", (media_id,)
            ).fetchone()
            if row:
                p = _jsonable_probe(row)
                p["lufs"] = lufs
                con.execute(
                    "UPDATE media SET probe_json = ? WHERE id = ?",
                    (json.dumps(p), media_id),
                )
                con.commit()
        finally:
            con.close()
        # peaks.json is written last: it is the "prep done" marker.
        (mdir / "peaks.json").write_text(
            json.dumps(compute_peaks(source)), encoding="utf-8"
        )
    except Exception:
        # Prep is best-effort; the upload itself already succeeded.
        pass


def _insufficient(needed: int, free: int) -> JSONResponse:
    return err(
        507, "insufficient_storage",
        f"Not enough disk space: needs about {needed / 1e9:.1f} GB,"
        f" {free / 1e9:.1f} GB free.",
    )


def _content_length(request: Request) -> int | None:
    raw = request.headers.get("content-length")
    try:
        return int(raw) if raw is not None else None
    except ValueError:
        return None


@router.post("/api/media", status_code=201)
async def upload_media(request: Request, file: UploadFile):
    settings = request.app.state.settings
    filename = file.filename or "upload"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_EXTS:
        return err(415, "unsupported_type", _MSG["unsupported_type"])

    content_length = _content_length(request)
    if content_length is not None:
        needed = content_length * 4 + _HEADROOM
        free = free_space_bytes(settings.data_dir)
        if free < needed:
            return _insufficient(needed, free)

    media_id = new_id()
    mdir = media_dir(media_id, settings.data_dir)
    mdir.mkdir(parents=True, exist_ok=True)
    original = mdir / f"original.{ext}"
    limit = settings.max_upload_mb << 20
    try:
        written = 0
        with original.open("wb") as fh:
            while chunk := await file.read(_CHUNK):
                written += len(chunk)
                if written > limit:
                    fh.close()
                    raise _TooLarge()
                fh.write(chunk)

        if content_length is None:  # no early check possible → verify now
            needed = written * 4 + _HEADROOM
            free = free_space_bytes(settings.data_dir)
            if free < needed:
                shutil.rmtree(mdir, ignore_errors=True)
                return _insufficient(needed, free)
        try:
            p = probe(original)
        except ProbeError:
            shutil.rmtree(mdir, ignore_errors=True)
            return err(422, "unreadable", _MSG["unreadable"])
        if not p.audio:
            shutil.rmtree(mdir, ignore_errors=True)
            return err(422, "no_audio", _MSG["no_audio"])

        con = connect(settings.db_path)
        try:
            con.execute(
                "INSERT INTO media (id, filename, stored_path, size_bytes,"
                " probe_json, created_at) VALUES (?,?,?,?,?,?)",
                (
                    media_id,
                    display_name(filename),
                    str(original),
                    written,
                    json.dumps(p.to_dict()),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            con.commit()
        finally:
            con.close()
    except _TooLarge:
        shutil.rmtree(mdir, ignore_errors=True)
        return err(413, "too_large", _MSG["too_large"])
    except Exception:
        shutil.rmtree(mdir, ignore_errors=True)
        raise

    threading.Thread(
        target=_prepare,
        args=(settings.db_path, mdir, media_id, p.audio[0].index),
        daemon=True,
    ).start()
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


@router.get("/api/media/{media_id}")
def get_media(request: Request, media_id: str):
    settings = request.app.state.settings
    row = _row(settings.db_path, media_id)
    if row is None:
        return err(404, "media_not_found", _MSG["media_not_found"])
    return _media_payload(row, media_dir(media_id, settings.data_dir))


@router.get("/api/media/{media_id}/peaks")
def get_peaks(request: Request, media_id: str, stream: int | None = None):
    settings = request.app.state.settings
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
            return err(202, "processing", "Still preparing this file.")
        return JSONResponse(json.loads(peaks_path.read_text(encoding="utf-8")))

    if stream not in {a["index"] for a in audio}:
        return err(422, "no_such_stream", _MSG["no_such_stream"])
    peaks_path = mdir / f"peaks_{stream}.json"
    if not peaks_path.is_file():
        originals = list(mdir.glob("original.*"))
        if not originals:
            return err(404, "media_not_found", _MSG["media_not_found"])
        stream_wav = mdir / f"stream_{stream}.wav"
        if not stream_wav.is_file():
            decode_to_wav(originals[0], stream_wav, stream)
        peaks_path.write_text(
            json.dumps(compute_peaks(stream_wav)), encoding="utf-8"
        )
    return JSONResponse(json.loads(peaks_path.read_text(encoding="utf-8")))


@router.get("/api/media/{media_id}/playback")
def get_playback(request: Request, media_id: str):
    settings = request.app.state.settings
    row = _row(settings.db_path, media_id)
    if row is None:
        return err(404, "media_not_found", _MSG["media_not_found"])
    playback = media_dir(media_id, settings.data_dir) / "playback.m4a"
    if not playback.is_file():
        return err(202, "processing", "Still preparing this file.")
    return _file_response(playback, request, "audio/mp4")


@router.get("/api/media/{media_id}/file")
def get_file(request: Request, media_id: str):
    settings = request.app.state.settings
    row = _row(settings.db_path, media_id)
    if row is None:
        return err(404, "media_not_found", _MSG["media_not_found"])
    path = Path(row["stored_path"])
    if not path.is_file():
        return err(404, "media_not_found", _MSG["media_not_found"])
    media_type = _MIME.get(
        path.suffix.lower()
    ) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return _file_response(path, request, media_type)


@router.delete("/api/media/{media_id}", status_code=204)
def delete_media(request: Request, media_id: str):
    settings = request.app.state.settings
    con = connect(settings.db_path)
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
        con.execute("DELETE FROM media WHERE id = ?", (media_id,))
        con.commit()
    finally:
        con.close()
    shutil.rmtree(media_dir(media_id, settings.data_dir), ignore_errors=True)
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
