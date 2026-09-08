"""Core-package constants: DB config, ORM table args, logging setup, timing thresholds."""

from __future__ import annotations

import os
from pathlib import Path

# Database connection (override via DATABASE_URL env var).
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://harshit@localhost:5432/trading")

# SQLModel: allow re-registration of table classes across Streamlit hot reloads.
TABLE_ARGS = {"extend_existing": True}

# Logging — rotating file handlers under LOGS_DIR.
LOGS_DIR = Path("logs")
LOG_MAX_BYTES = 5 * 1024 * 1024  # 5 MB per file
LOG_BACKUP_COUNT = 3
LOG_FORMAT = "%(asctime)s | %(name)s | %(levelname)s | %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
LOG_FILES = {
    "app": "app.log",  # general app lifecycle (catches everything)
    "data": "data.log",  # data layer — the `data` logger subtree (fetchers + repositories)
    "perf": "perf.log",  # phase/function timing from core.timing
}
# Marker tagged on every handler we attach, so setup is idempotent across module re-imports.
# Keep the value stable across layout changes: a bump would add a second handler set during
# a live hot-reload session (old-marked handlers stay); a restart picks up new layouts anyway.
LOG_HANDLER_MARKER = "_app_logging_v1"


def _env_flag(name: str, *, default: bool) -> bool:
    """Read a boolean env var. Accepts 1/true/yes/on (and their negatives), case-insensitive."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


# Log verbosity, all overridable from the environment.
#   LOG_LEVEL=DEBUG   raise the file-log level (default INFO)
#   PERF_LOG=1        write logs/perf.log and time the blocks core.timing wraps (default off)
#   DEBUG_LOG=1       shorthand for LOG_LEVEL=DEBUG
# perf timing is off by default: it logged every wrapped call on every Streamlit rerun, which
# filled 5 MB of perf.log per session while only ever being read when chasing a slow page.
DEBUG_LOG = _env_flag("DEBUG_LOG", default=False)
LOG_LEVEL = os.environ.get("LOG_LEVEL", "DEBUG" if DEBUG_LOG else "INFO").upper()
PERF_LOG_ENABLED = _env_flag("PERF_LOG", default=False)

# Timing (core.timing): calls slower than this log at INFO, faster ones at DEBUG.
DEFAULT_SLOW_MS = 100.0
