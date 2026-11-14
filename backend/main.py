"""FastAPI app factory (SPEC §4/§5): init DB, mount API routers, serve SPA."""
from __future__ import annotations

import os

from fastapi import FastAPI, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.api.health import router as health_router
from backend.api.operations import router as operations_router
from backend.core.models_manifest import configure_model_env
from backend.core.paths import ROOT
from backend.core.settings import Settings, get_settings
from backend.db import init_db

FRONTEND_DIST = ROOT / "frontend" / "dist"

# Review Focus 2: plain 200 page when the frontend hasn't been built.
_FALLBACK_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sound System</title>
<style>
  body{background:#000;color:#fff;font:16px/1.5 system-ui,sans-serif;
       display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0}
  main{max-width:36em;padding:2em}
  code{background:#222;padding:.1em .4em;border-radius:4px}
  a{color:#fff}
</style>
</head>
<body><main>
<h1>Sound System</h1>
<p>Frontend not built — run scripts/setup.ps1</p>
<p>The API is still up: <a href="/api/health">/api/health</a></p>
</main></body>
</html>"""


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
        if path == "favicon.ico":
            return Response(status_code=404)
        if path == "api" or path.startswith("api/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        if index.is_file():
            return FileResponse(index)
        return HTMLResponse(_FALLBACK_HTML)


def create_app(settings: Settings | None = None) -> FastAPI:
    s = settings or get_settings()
    configure_model_env()
    _prepend_ffmpeg_dir()
    init_db(s.db_path)
    app = FastAPI(title="Sound System")
    app.include_router(health_router)
    app.include_router(operations_router)
    _mount_spa(app)
    return app


app = create_app()
