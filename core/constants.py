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

# Timing (core.timing): calls slower than this log at INFO, faster ones at DEBUG.
DEFAULT_SLOW_MS = 100.0
