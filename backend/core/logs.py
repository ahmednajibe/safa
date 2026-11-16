"""M10 T7: bounded logs + privacy-safe diagnostics.

``setup_app_logging`` attaches one rotating handler (5 MB x 3 files) at
``<data>/logs/app.log`` on the ``sound_system`` logger. ``bound_log``
head-truncates any oversized log (per-job ``worker.log``), so a chatty
traceback can never grow without limit.

``collect_diagnostics`` builds the JSON behind "Copy diagnostics":
versions, capability report, settings, recent job states and the last
50 lines of ``app.log`` plus the newest ``worker.log`` — with the user
profile path, the account name and every media filename replaced by
``<redacted>`` before anything leaves the machine.
"""
from __future__ import annotations

import getpass
import json
import logging
import os
import platform
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

APP_LOG_MAX = 5 * 1024 * 1024
APP_LOG_BACKUPS = 3  # app.log + app.log.1..3
WORKER_LOG_MAX = 1 * 1024 * 1024
_TAIL_LINES = 50
_TRUNC_MARK = "... [earlier log lines dropped] ...\n"

_log = logging.getLogger("sound_system")
_handler_path: Path | None = None


def logs_dir(data_dir: Path) -> Path:
    d = Path(data_dir) / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def app_log_path(data_dir: Path) -> Path:
    return logs_dir(data_dir) / "app.log"


def setup_app_logging(data_dir: Path) -> Path:
    """Attach the rotating app-log handler; returns the log path.

    Idempotent per path — tests create many app instances against
    different data dirs; a new path swaps the stale handler instead of
    stacking them (a handler pointing at a deleted tmp dir would warn
    on every emit)."""
    global _handler_path
    path = app_log_path(data_dir)
    if _handler_path == path:
        return path
    for h in list(_log.handlers):
        _log.removeHandler(h)
        try:
            h.close()
        except Exception:
            pass
    h = RotatingFileHandler(
        path,
        maxBytes=APP_LOG_MAX,
        backupCount=APP_LOG_BACKUPS,
        encoding="utf-8",
    )
    h.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    _log.addHandler(h)
    _log.setLevel(logging.INFO)
    _log.propagate = False  # keep diagnostics text out of pytest captures
    _handler_path = path
    return path


def bound_log(path: Path, cap: int = WORKER_LOG_MAX) -> None:
    """Keep ``path`` under ``cap`` bytes by dropping its head (leaves a
    marker line so a truncated file reads as intentional)."""
    try:
        if not path.is_file() or path.stat().st_size <= cap:
            return
        with path.open("rb") as fh:
            fh.seek(-(cap // 2), os.SEEK_END)
            tail = fh.read()
        nl = tail.find(b"\n")
        if 0 <= nl < len(tail) - 1:
            tail = tail[nl + 1 :]
        path.write_bytes(_TRUNC_MARK.encode("utf-8") + tail)
    except OSError:
        pass


# ---- diagnostics ------------------------------------------------------------


def _secret_strings(store=None) -> list[str]:
    """Exact strings that must never appear in copied diagnostics."""
    out: list[str] = []
    for var in ("USERPROFILE", "HOME", "HOMEPATH"):
        v = os.environ.get(var)
        if v:
            out += [v, v.replace("\\", "/")]
    try:
        out.append(getpass.getuser())
    except Exception:
        pass
    for var in ("USERNAME", "USER", "LOGNAME"):
        v = os.environ.get(var)
        if v:
            out.append(v)
    if store is not None:
        out += [f for f in store.media_filenames() if f]
        od = store.get_setting("output_dir")
        if od:
            out += [od, od.replace("\\", "/")]
    # longest first so "C:\Users\x\media\song.wav" redacts at the path
    # level before the bare filename rule can half-mask it
    return sorted({s for s in out if s}, key=len, reverse=True)


def redact_text(text: str, secrets: list[str]) -> str:
    for s in secrets:
        if len(s) < 3:
            continue
        text = re.sub(re.escape(s), "<redacted>", text, flags=re.IGNORECASE)
    return text


def _tail(path: Path, n: int, secrets: list[str]) -> list[str] | None:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    return [redact_text(line, secrets) for line in lines[-n:]]


def _newest_worker_log(data_dir: Path) -> Path | None:
    jobs = Path(data_dir) / "jobs"
    try:
        logs = [
            p
            for p in jobs.glob("*/worker.log")
            if p.is_file() and not p.is_symlink()
        ]
    except OSError:
        return None
    return max(logs, key=lambda p: p.stat().st_mtime) if logs else None


def collect_diagnostics(settings, store) -> dict:
    """Redacted JSON for the "Copy diagnostics" button (M10 T7)."""
    from datetime import datetime, timezone

    from backend.core.capabilities import detect_capabilities
    from backend.pipeline.device import gpu_usable

    secrets = _secret_strings(store)
    caps = detect_capabilities()
    ffmpeg = {
        k: v
        for k, v in (caps.ffmpeg or {}).items()
        if k in ("version", "nvenc", "error")
    }
    jobs = [
        {
            "state": j.state,
            "kind": j.kind,
            "ops": [o.get("id") for o in j.ops],
            "error_code": j.error_code,
            "created_at": j.created_at,
            "finished_at": j.finished_at,
        }
        for j in store.list(20)
    ]
    worker_log = _newest_worker_log(settings.data_dir)
    return {
        "app": "sound-system",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "python": caps.python,
        "os": f"{platform.system()} {platform.release()}",
        "device": "gpu" if gpu_usable(caps) else "cpu",
        "gpu": caps.gpu,
        "cuda_dlls": caps.cuda_dlls,
        "ffmpeg": ffmpeg,
        "modules": caps.modules,
        "models": caps.models,
        "settings": {
            "port": settings.port,
            "retention_days": store.get_setting("retention_days")
            or settings.retention_days,
            "output_dir": bool(store.get_setting("output_dir")),
            "data_dir": redact_text(str(settings.data_dir), secrets),
        },
        "recent_jobs": json.loads(redact_text(json.dumps(jobs), secrets)),
        "log_tail": _tail(app_log_path(settings.data_dir), _TAIL_LINES, secrets),
        "worker_log_tail": (
            _tail(worker_log, _TAIL_LINES, secrets) if worker_log else None
        ),
    }
