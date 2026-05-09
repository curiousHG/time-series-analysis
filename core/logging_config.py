"""Centralized logging setup. Import and call setup_logging() once at app start."""

import logging
import sys
import warnings
from logging.handlers import RotatingFileHandler
from pathlib import Path

from sqlalchemy.exc import SAWarning

# Streamlit re-imports modules on hot reload, which makes SQLModel re-register classes
# in SQLAlchemy's declarative base. The redefinition is identical — the warning is noise.
warnings.filterwarnings(
    "ignore",
    message=r"This declarative base already contains a class with the same class name.*",
    category=SAWarning,
)

LOGS_DIR = Path("logs")
MAX_BYTES = 5 * 1024 * 1024  # 5 MB per file
BACKUP_COUNT = 3
FMT = "%(asctime)s | %(name)s | %(levelname)s | %(message)s"
DATE_FMT = "%Y-%m-%d %H:%M:%S"

# Log file categories
LOG_FILES = {
    "app": "app.log",  # general app lifecycle
    "data": "data.log",  # data fetching, storage, API calls
    "ui": "ui.log",  # UI events, state changes
}

# Marker attribute we tag every handler we attach with — survives module re-imports
# because Python's `logging` registry holds the same logger objects across imports.
_HANDLER_MARKER = "_app_logging_v1"


def _has_marked_handler(logger: logging.Logger) -> bool:
    return any(getattr(h, _HANDLER_MARKER, False) for h in logger.handlers)


def _mark(handler: logging.Handler) -> logging.Handler:
    setattr(handler, _HANDLER_MARKER, True)
    return handler


def setup_logging(level: int = logging.INFO):
    """Configure logging with rotating file handlers. Idempotent across Streamlit hot reloads.

    The guard checks the actual logger state (via a marker attribute on attached handlers)
    instead of a module-level flag — Streamlit's file watcher re-imports modules and resets
    module-level globals, so a `_initialized = True` flag would silently let handlers pile up
    on every save (causing N-fold log line duplication).
    """
    root = logging.getLogger()
    if _has_marked_handler(root):
        return
    root.setLevel(level)

    LOGS_DIR.mkdir(exist_ok=True)
    formatter = logging.Formatter(FMT, datefmt=DATE_FMT)

    # Console handler (WARNING+ only to avoid cluttering Streamlit)
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.WARNING)
    console.setFormatter(formatter)
    root.addHandler(_mark(console))

    # app.log — catches everything
    app_handler = RotatingFileHandler(LOGS_DIR / LOG_FILES["app"], maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT)
    app_handler.setLevel(level)
    app_handler.setFormatter(formatter)
    root.addHandler(_mark(app_handler))

    # data.log — data fetchers and store
    data_handler = RotatingFileHandler(LOGS_DIR / LOG_FILES["data"], maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT)
    data_handler.setLevel(logging.DEBUG)
    data_handler.setFormatter(formatter)
    for namespace in ("data.fetchers", "data.store"):
        ns_logger = logging.getLogger(namespace)
        if not _has_marked_handler(ns_logger):
            ns_logger.addHandler(_mark(data_handler))

    # ui.log — UI components and views
    ui_handler = RotatingFileHandler(LOGS_DIR / LOG_FILES["ui"], maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT)
    ui_handler.setLevel(logging.DEBUG)
    ui_handler.setFormatter(formatter)
    ui_logger = logging.getLogger("ui")
    if not _has_marked_handler(ui_logger):
        ui_logger.addHandler(_mark(ui_handler))


def get_logger(name: str) -> logging.Logger:
    """Get a logger. Call setup_logging() first."""
    return logging.getLogger(name)
