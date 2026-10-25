"""Core-package constants: DB config, ORM table args, logging setup, timing thresholds."""

from __future__ import annotations

import os
from pathlib import Path

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://harshit@localhost:5432/trading")

TRADING_DAYS = 252

TABLE_ARGS = {"extend_existing": True}

LOGS_DIR = Path("logs")
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 3
LOG_FORMAT = "%(asctime)s | %(name)s | %(levelname)s | %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
LOG_FILES = {
    "app": "app.log",
    "data": "data.log",
    "perf": "perf.log",
}
LOG_HANDLER_MARKER = "_app_logging_v1"


def _env_flag(name: str, *, default: bool) -> bool:
    """Read a boolean env var. Accepts 1/true/yes/on (and their negatives), case-insensitive."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


DEBUG_LOG = _env_flag("DEBUG_LOG", default=False)
LOG_LEVEL = os.environ.get("LOG_LEVEL", "DEBUG" if DEBUG_LOG else "INFO").upper()
PERF_LOG_ENABLED = _env_flag("PERF_LOG", default=False)
LOG_CONSOLE_ENABLED = _env_flag("LOG_CONSOLE", default=False)

DEFAULT_SLOW_MS = 100.0
