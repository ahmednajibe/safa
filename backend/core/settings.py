"""App settings (SPEC §4): loopback-only server, data under ``data/``."""
import os
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

from backend.core.paths import DATA_DIR


def _default_port() -> int:
    """Server port: ``SOUND_SYSTEM_PORT`` env override, default 8790
    (8765 is permanently used by another app on this machine)."""
    raw = os.environ.get("SOUND_SYSTEM_PORT", "").strip()
    if not raw:
        return 8790
    try:
        port = int(raw)
    except ValueError:
        raise ValueError(
            f"SOUND_SYSTEM_PORT must be a port number (1-65535), got {raw!r}"
        ) from None
    if not 1 <= port <= 65535:
        raise ValueError(
            f"SOUND_SYSTEM_PORT must be a port number (1-65535), got {raw!r}"
        )
    return port


def _default_bind_host() -> str:
    """Bind address: ``SOUND_SYSTEM_BIND_HOST`` env override, default
    ``127.0.0.1``. Native installs always stay loopback-only; only the
    container image sets ``0.0.0.0`` (compose publishes loopback on the
    host). The Host/Origin guard is independent of this value."""
    raw = os.environ.get("SOUND_SYSTEM_BIND_HOST", "").strip()
    return raw or "127.0.0.1"


@dataclass
class Settings:
    host: str = field(default_factory=_default_bind_host)
    port: int = field(default_factory=_default_port)
    data_dir: Path = DATA_DIR
    db_path: Path = DATA_DIR / "app.db"
    retention_days: int = 7
    max_upload_mb: int = 4096


@cache
def get_settings() -> Settings:
    return Settings()
