# Dockerfile — Safa CPU image (M10c §9.17). GPU variant: Dockerfile.cuda.
#   docker build -f Dockerfile -t safa:cpu .
#   docker run --rm -p 127.0.0.1:8790:8790 -v safa-data:/data safa:cpu
#
# Multi-stage: node builds frontend/dist, slim python:3.11 runs the app.
# Inside the container uvicorn binds 0.0.0.0 (required); the HOST-side
# mapping must stay 127.0.0.1:8790:8790 — see docker-compose.yml.

# ---- frontend build -----------------------------------------------------------
FROM node:22-bookworm-slim AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- runtime -------------------------------------------------------------------
FROM python:3.11-slim-bookworm AS runtime
ARG EXTRA=cpu
ENV PYTHONUTF8=1 \
    SOUND_SYSTEM_PORT=8790 \
    SOUND_SYSTEM_BIND_HOST=0.0.0.0 \
    SOUND_SYSTEM_IN_CONTAINER=1 \
    SOUND_SYSTEM_DATA_DIR=/data

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Exact pin matching the dev env's uv (see AGENTS.md) — never a floating tag.
COPY --from=ghcr.io/astral-sh/uv:0.12.19 /uv /uvx /usr/local/bin/

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY backend/ backend/
COPY scripts/prefetch_models.py scripts/prefetch_models.py
RUN uv sync --frozen --no-dev --extra "$EXTRA" \
    && uv cache clean

COPY --from=frontend /build/frontend/dist frontend/dist

# ffmpeg/ffprobe: the backend probes tools/ffmpeg/bin first, then PATH —
# the apt binaries land on PATH, no copying needed.

RUN useradd --create-home --uid 10001 safa \
    && mkdir -p /data && chown safa:safa /data
USER safa
VOLUME ["/data"]
EXPOSE 8790

HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=5 \
    CMD ["/app/.venv/bin/python", "-c", "import urllib.request,os;urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('SOUND_SYSTEM_PORT','8790')+'/api/health',timeout=4)"]

CMD ["uv", "run", "--frozen", "--no-sync", "python", "-m", "uvicorn", \
     "backend.main:app", "--host", "0.0.0.0", "--port", "8790"]
