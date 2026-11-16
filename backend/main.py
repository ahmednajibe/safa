"""FastAPI app factory (SPEC §4/§5): init DB, mount API routers, serve SPA."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.api.errors import err
from backend.api.diagnostics import router as diagnostics_router
from backend.api.estimate import router as estimate_router
from backend.api.health import router as health_router
from backend.api.jobs import router as jobs_router
from backend.api import media as media_api
from backend.api.media import router as media_router
from backend.api.models import ModelDownloadManager
from backend.api.models import router as models_router
from backend.api.operations import router as operations_router
from backend.api.settings import router as settings_router
from backend.core.logs import setup_app_logging
from backend.core.messages import MESSAGES as _MSG
from backend.core.models_manifest import configure_model_env
from backend.core.paths import ROOT, ensure_data_marker, redirect_temp_env
from backend.core.retention import RetentionSweeper, retention_days
from backend.core.settings import Settings, get_settings
from backend.db import init_db
from backend.jobs.store import JobStore
from backend.jobs.supervisor import Supervisor

FRONTEND_DIST = ROOT / "frontend" / "dist"

# Review Focus 2: plain 200 page when the frontend hasn't been built.
_FALLBACK_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Safa (صفا)</title>
<style>
  body{background:#000;color:#fff;font:16px/1.5 system-ui,sans-serif;
       display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0}
  main{max-width:36em;padding:2em}
  code{background:#222;padding:.1em .4em;border-radius:4px}
  a{color:#fff}
</style>
</head>
<body><main>
<h1>Safa (صفا)</h1>
<p>Frontend not built — run scripts/setup.ps1</p>
<p>The API is still up: <a href="/api/health">/api/health</a></p>
</main></body>
</html>"""


# ---- R1 A1: loopback Host + same-origin guard -------------------------------
#
# DNS rebinding lets a web page at attacker.example (resolving to 127.0.0.1)
# call this API; CORS-simple POSTs (cancel/retry/delete) need no preflight.
# Every request must carry a loopback Host for our port; mutating requests
# must additionally come from our own origin — or present no browser origin
# headers at all (curl/TestClient) — and never ``Sec-Fetch-Site: cross-site``.

_MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_LOOPBACK_NAMES = {"127.0.0.1", "localhost"}


def _host_port(authority: str) -> tuple[str, int | None]:
    """Split a Host/Origin authority into (hostname, port|None)."""
    authority = authority.strip().lower()
    if authority.startswith("["):  # [::1]:8790
        name, _, rest = authority.partition("]")
        port_s = rest.lstrip(":")
    else:
        name, _, port_s = authority.partition(":")
    try:
        return name, int(port_s) if port_s else None
    except ValueError:
        return name, -1


def _loopback_host(authority: str | None, port: int) -> bool:
    """True for exactly ``127.0.0.1:<port>`` / ``localhost:<port>``."""
    if not authority:
        return False
    name, hport = _host_port(authority)
    return name in _LOOPBACK_NAMES and hport == port


def _same_origin(url: str | None, port: int) -> bool:
    if not url:
        return False
    return _loopback_host(urlsplit(url).netloc, port)


_LOOPBACK_BINDS = {"127.0.0.1", "::1", "localhost"}


def _warn_if_wide_bind(s: Settings) -> None:
    """A non-loopback bind on a native install is almost always a mistake —
    log a loud warning (visibility only; the Host/Origin guard below is
    unchanged). ``SOUND_SYSTEM_IN_CONTAINER=1`` marks the Docker image's
    intentional ``0.0.0.0`` bind."""
    host = (s.host or "").strip().lower()
    if not host or host in _LOOPBACK_BINDS:
        return
    if os.environ.get("SOUND_SYSTEM_IN_CONTAINER", "").strip() == "1":
        return
    logging.getLogger("sound_system").warning(
        "SOUND_SYSTEM_BIND_HOST=%s — the API will listen on a "
        "network-reachable interface, not just this computer. Native "
        "installs should keep 127.0.0.1 (inside containers set "
        "SOUND_SYSTEM_IN_CONTAINER=1 to silence this).",
        host,
    )


def _prepend_ffmpeg_dir() -> None:
    """Put ``tools/ffmpeg/bin`` first on PATH (audio-separator/pydub shell out
    to a bare ``ffmpeg``; FEASIBILITY "Workarounds" #2). Idempotent."""
    try:
        from backend.core.ffmpeg import find_ffmpeg

        bin_dir = str(find_ffmpeg().parent)
    except Exception:
        return
    if bin_dir not in os.environ.get("PATH", "").split(os.pathsep):
        os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")


# Dist-root files browsers request by name — a fixed allow-list (never
# path-derived), so no generic root-file serving and no traversal.
_DIST_ROOT_FILES = {
    "favicon.ico": "image/x-icon",
    "favicon.svg": "image/svg+xml",
    "apple-touch-icon.png": "image/png",
}


def _mount_spa(app: FastAPI) -> None:
    """Serve the built React app; SPA fallback for every non-/api GET."""
    dist = FRONTEND_DIST
    index = dist / "index.html"
    assets = dist / "assets"
    if assets.is_dir():
        app.mount(
            "/assets", StaticFiles(directory=str(assets)), name="assets"
        )

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> Response:
        media = _DIST_ROOT_FILES.get(path)
        if media is not None:
            f = dist / path
            if f.is_file():
                return FileResponse(f, media_type=media)
            return Response(status_code=404)
        if path == "api" or path.startswith("api/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        if index.is_file():
            return FileResponse(index)
        return HTMLResponse(_FALLBACK_HTML)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """On boot: corpse running jobs → interrupted, then start the supervisor.
    On shutdown: stop the supervisor (kills the running worker tree, marks
    the job interrupted)."""
    # R1 B3: kill worker processes the previous (crashed) server left
    # running *before* their job rows are marked interrupted.
    app.state.supervisor.kill_orphan_workers()
    app.state.store.mark_interrupted_on_startup()
    app.state.supervisor.start()
    # M10 T6: retention sweep at startup, then every 24 h — media mid-prep
    # is reported as skip_media so it can never be swept under the prep thread.
    app.state.sweeper = RetentionSweeper(
        app.state.store,
        app.state.settings.data_dir,
        lambda: retention_days(app.state.store, app.state.settings),
        lambda: set(media_api._PREP_THREADS),
    )
    app.state.sweeper.start()
    logging.getLogger("sound_system").info(
        "app started on %s:%s",
        app.state.settings.host, app.state.settings.port,
    )
    try:
        yield
    finally:
        app.state.model_downloads.stop()
        app.state.sweeper.stop()
        app.state.supervisor.stop()
        logging.getLogger("sound_system").info("app stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    s = settings or get_settings()
    _warn_if_wide_bind(s)
    ensure_data_marker()
    configure_model_env()
    redirect_temp_env(s.data_dir)
    _prepend_ffmpeg_dir()
    init_db(s.db_path)
    setup_app_logging(s.data_dir)
    app = FastAPI(title="Safa", lifespan=_lifespan)
    app.state.settings = s
    app.state.store = JobStore(s.db_path)
    app.state.supervisor = Supervisor(app.state.store, s)
    app.state.model_downloads = ModelDownloadManager()

    @app.middleware("http")
    async def host_origin_guard(request: Request, call_next):
        # Runs before any body is read: bad Host → 400, cross-site or
        # foreign-Origin mutating request → 403 (R1 A1).
        port = request.app.state.settings.port
        if not _loopback_host(request.headers.get("host"), port):
            return err(400, "bad_host", _MSG["bad_host"])
        if request.method in _MUTATING_METHODS:
            if (
                request.headers.get("sec-fetch-site", "").lower()
                == "cross-site"
            ):
                return err(403, "bad_origin", _MSG["bad_origin"])
            src = (
                request.headers.get("origin")
                or request.headers.get("referer")
            )
            if src is not None and not _same_origin(src, port):
                return err(403, "bad_origin", _MSG["bad_origin"])
        return await call_next(request)

    app.include_router(health_router)
    app.include_router(operations_router)
    app.include_router(media_router)
    app.include_router(estimate_router)
    app.include_router(jobs_router)
    app.include_router(settings_router)
    app.include_router(models_router)
    app.include_router(diagnostics_router)
    _mount_spa(app)
    return app


app = create_app()
