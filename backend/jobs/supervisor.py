"""Job supervisor (M3b plan Task 3): one worker subprocess at a time.

A daemon thread inside the API process picks the oldest queued job and
spawns ``python -m backend.worker <job_id>``; it blocks on that process, so
at most one worker ever runs (4 GB VRAM budget, SPEC §4). Cancel = DB flag
+ process-tree kill (ffmpeg children included). Shutdown marks the running
job ``interrupted`` so the next start can offer a retry.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import psutil

from backend.core.paths import ROOT
from backend.core.settings import Settings
from backend.jobs.models import TERMINAL, Job
from backend.jobs.store import JobStore
from backend.media.storage import job_dir

_INTERRUPTED_MSG = "Stopped because the app was closed"
_CRASH_MSG = "The processing step stopped unexpectedly"

if sys.platform == "win32":
    # Own process group + no console window, so the tree can be killed
    # cleanly without touching this console or popping windows.
    _CREATEFLAGS = (
        subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    )
else:
    _CREATEFLAGS = 0


def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill ``proc`` and every descendant (parent first — a dead worker can't
    write a misleading ``failed`` state while its ffmpeg dies)."""
    try:
        parent = psutil.Process(proc.pid)
        children = parent.children(recursive=True)
    except psutil.NoSuchProcess:
        children = []
        parent = None
    try:
        proc.kill()
    except Exception:
        pass
    if parent is not None:
        try:
            parent.kill()
        except psutil.NoSuchProcess:
            pass
    for child in children:
        try:
            child.kill()
        except psutil.NoSuchProcess:
            pass


class Supervisor:
    def __init__(self, store: JobStore, settings: Settings):
        self.store = store
        self.settings = settings
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._running_job_id: str | None = None
        self._thread: threading.Thread | None = None

    # ---- API ---------------------------------------------------------------

    @property
    def running_job_id(self) -> str | None:
        with self._lock:
            return self._running_job_id

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="job-supervisor", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 10) -> None:
        """Stop the loop, kill the running worker tree, mark it interrupted."""
        self._stop.set()
        self._wake.set()
        with self._lock:
            proc, job_id = self._proc, self._running_job_id
        if proc is not None and proc.poll() is None:
            _kill_tree(proc)
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        # If the loop never got to post-process the corpse, mark it here.
        if job_id:
            job = self.store.get(job_id)
            if job is not None and job.state not in TERMINAL:
                self.store.finish(
                    job_id, "interrupted", message=_INTERRUPTED_MSG
                )

    def wake(self) -> None:
        """Nudge the loop after job creation."""
        self._wake.set()

    def cancel(self, job_id: str) -> None:
        """Set the cancel flag; give the worker 2 s to exit cleanly, then
        kill its whole process tree (ffmpeg children included). A queued job
        is marked cancelled immediately — no worker spawn wasted."""
        self.store.request_cancel(job_id)
        job = self.store.get(job_id)
        if job is not None and job.state == "queued":
            self.store.finish(job_id, "cancelled")
            return
        with self._lock:
            proc = (
                self._proc if self._running_job_id == job_id else None
            )
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        job = self.store.get(job_id)
        if (
            job is not None
            and job.state != "completed"
            and job.state != "cancelled"
        ):
            self._mark_cancelled(job_id)

    def worker_env(self, job: Job) -> dict[str, str]:
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["SOUND_SYSTEM_DB"] = str(self.settings.db_path)
        env["SOUND_SYSTEM_DATA"] = str(self.settings.data_dir)
        if job.requested_device == "cpu":
            env["CUDA_VISIBLE_DEVICES"] = ""  # SPEC §9 hard ceiling
        return env

    # ---- loop -----------------------------------------------------------------

    def _mark_cancelled(self, job_id: str) -> None:
        """Mark cancelled and drop partial outputs — a killed worker can't
        clean up after itself (Review Focus 1)."""
        self.store.finish(job_id, "cancelled")
        for sub in ("output", "work"):
            d = self.settings.data_dir / "jobs" / job_id / sub
            if not d.is_dir():
                continue
            for child in d.iterdir():
                try:
                    if child.is_dir() and not child.is_symlink():
                        shutil.rmtree(child, ignore_errors=True)
                    else:
                        child.unlink(missing_ok=True)
                except OSError:
                    pass

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(0.5)
            self._wake.clear()
            if self._stop.is_set():
                break
            job = self.store.next_queued()
            if job is None:
                continue
            try:
                self._run_job(job)
            except Exception:
                # A spawn failure must not kill the supervisor thread —
                # the next queued job still gets its turn.
                try:
                    self.store.finish(
                        job.id, "failed", error=_CRASH_MSG
                    )
                except Exception:
                    pass

    def _run_job(self, job: Job) -> None:
        if self._stop.is_set():
            return  # leave it queued for the next start
        if self.store.cancel_requested(job.id):
            self._mark_cancelled(job.id)
            return
        log_path = job_dir(job.id, self.settings.data_dir) / "worker.log"
        with log_path.open("ab") as logf:
            proc = subprocess.Popen(
                [sys.executable, "-m", "backend.worker", job.id],
                cwd=str(ROOT),
                env=self.worker_env(job),
                stdout=logf,
                stderr=subprocess.STDOUT,
                creationflags=_CREATEFLAGS,
            )
        with self._lock:
            self._proc = proc
            self._running_job_id = job.id
        if self._stop.is_set():
            # stop() ran between Popen and the lock — kill now or the worker
            # is orphaned and could resurrect the job we mark interrupted.
            _kill_tree(proc)
        try:
            self.store.mark_running(job.id, proc.pid)
            proc.wait()
        finally:
            with self._lock:
                self._proc = None
                self._running_job_id = None
        self._after_exit(job.id)

    def _after_exit(self, job_id: str) -> None:
        """Reconcile the job row after the worker exits / is killed."""
        job = self.store.get(job_id)
        if job is None or job.state == "completed":
            return
        if self._stop.is_set():
            if job.state not in TERMINAL:
                self.store.finish(
                    job_id, "interrupted", message=_INTERRUPTED_MSG
                )
            return
        if self.store.cancel_requested(job_id):
            # Covers the kill race too: a worker that caught its ffmpeg dying
            # may have written 'failed' before it was terminated.
            if job.state != "cancelled":
                self._mark_cancelled(job_id)
            return
        if job.state not in TERMINAL:
            self.store.finish(job_id, "failed", error=_CRASH_MSG)
