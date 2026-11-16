"""M10 T6: retention sweep — bounded storage for a local app.

Deletes terminal jobs (folder + row) older than ``retention_days`` by
``finished_at``, then media rows/dirs no job references. Queued/running
jobs, kept jobs, media being prepared, downloads and ``saved_path``
copies (outside ``data/`` anyway) are never touched. Deletion is
junction/symlink-safe: links are dropped, never followed, and resolved
paths must stay under ``data/``.
"""
from __future__ import annotations

import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.jobs.models import TERMINAL

RETENTION_CHOICES: tuple[int | str, ...] = (1, 7, 30, "never")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def retention_days(store, settings) -> int | None:
    """Persisted retention setting → days, or ``None`` for "never"."""
    raw = store.get_setting("retention_days")
    if raw in (None, ""):
        days = settings.retention_days
    elif raw == "never":
        return None
    else:
        try:
            days = int(raw)
        except ValueError:
            days = settings.retention_days
    return None if days is None else max(1, int(days))


# ---- link/junction-safe deletion ------------------------------------------------


def _is_link(p: Path) -> bool:
    """Symlink — or a Windows junction (``is_dir`` whose real path is
    elsewhere; ``is_symlink`` alone misses junctions on Python 3.11)."""
    try:
        if p.is_symlink():
            return True
        return p.is_dir() and os.path.realpath(p) != os.path.abspath(p)
    except OSError:
        return True  # can't prove it isn't a link → don't recurse


def _drop_link(p: Path) -> None:
    """Remove the link itself. ``os.rmdir`` removes a junction/dir-symlink
    without touching its target."""
    try:
        if p.is_dir() and not p.is_symlink():
            os.rmdir(p)
        elif p.is_dir():
            # dir symlink on POSIX: rmdir removes the link, not contents
            os.rmdir(p)
        else:
            p.unlink(missing_ok=True)
    except OSError:
        pass


def _rmtree_safe(path: Path, root: Path) -> int:
    """Recursively delete ``path`` without ever following links out of
    ``root``; returns bytes freed. A link at ``path`` itself is unlinked
    (target untouched); a real dir that *resolves* outside is refused."""
    if _is_link(path):
        _drop_link(path)
        return 0
    root_res = root.resolve()
    try:
        resolved = path.resolve()
    except OSError:
        return 0
    if resolved != root_res and root_res not in resolved.parents:
        return 0
    if not path.is_dir():
        return 0
    freed = 0
    try:
        children = list(path.iterdir())
    except OSError:
        return 0
    for child in children:
        try:
            if _is_link(child):
                _drop_link(child)
            elif child.is_dir():
                freed += _rmtree_safe(child, root)
            else:
                try:
                    freed += child.stat().st_size
                except OSError:
                    pass
                child.unlink(missing_ok=True)
        except OSError:
            pass
    try:
        path.rmdir()
    except OSError:
        pass
    return freed


# ---- sweep -----------------------------------------------------------------------


def _expired(job, cutoff: datetime) -> bool:
    if job is None or job.state not in TERMINAL or job.keep:
        return False
    fin = _parse_iso(job.finished_at)
    return fin is not None and fin < cutoff


def sweep(
    store,
    data_dir: Path,
    days: int | None,
    *,
    now: datetime | None = None,
    skip_media: set[str] | None = None,
) -> dict:
    """One sweep pass. ``days=None`` ("never") deletes nothing."""
    stats = {"jobs": 0, "media": 0, "freed_bytes": 0}
    if days is None:
        return stats
    cutoff = (now or _utcnow()) - timedelta(days=days)

    for job in store.expired_terminal_jobs(cutoff.isoformat()):
        # Race safety: re-read the row — a retry/cancel may have flipped
        # it back to live between the listing and this delete.
        if not _expired(store.get(job.id), cutoff):
            continue
        stats["freed_bytes"] += _rmtree_safe(
            data_dir / "jobs" / job.id, data_dir
        )
        store.delete(job.id)
        stats["jobs"] += 1

    skip = set(skip_media or ())
    # Only media older than the cutoff: a fresh upload has no job yet
    # (the user is still choosing steps) and must never be swept.
    for mid in store.unreferenced_media_ids(cutoff.isoformat()):
        if mid in skip:
            continue
        stats["freed_bytes"] += _rmtree_safe(
            data_dir / "media" / mid, data_dir
        )
        store.delete_media(mid)
        stats["media"] += 1
    return stats


class RetentionSweeper:
    """Startup + every-24-h sweep on a daemon thread (M10 T6)."""

    PERIOD_S = 24 * 3600

    def __init__(
        self,
        store,
        data_dir: Path,
        days_fn,
        skip_media_fn=None,
        period_s: float | None = None,
    ):
        self.store = store
        self.data_dir = data_dir
        self._days_fn = days_fn
        self._skip_media_fn = skip_media_fn or (lambda: set())
        if period_s is not None:
            self.PERIOD_S = period_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.stats: dict | None = None

    def run_once(self) -> dict:
        self.stats = sweep(
            self.store,
            self.data_dir,
            self._days_fn(),
            skip_media=self._skip_media_fn(),
        )
        return self.stats

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="retention-sweeper", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 5) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def _loop(self) -> None:
        try:
            self.run_once()  # startup sweep
        except Exception:
            pass  # a failed sweep must never kill the app
        while not self._stop.wait(self.PERIOD_S):
            try:
                self.run_once()
            except Exception:
                pass
