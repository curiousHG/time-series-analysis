"""Centralized logging setup. Import and call setup_logging() once at app start."""

import logging
import sys
import warnings
from logging.handlers import RotatingFileHandler

from sqlalchemy.exc import SAWarning

from core.constants import (
    LOG_BACKUP_COUNT,
    LOG_DATE_FORMAT,
    LOG_FILES,
    LOG_FORMAT,
    LOG_HANDLER_MARKER,
    LOG_MAX_BYTES,
    LOGS_DIR,
)

# Streamlit re-imports modules on hot reload, which makes SQLModel re-register classes
# in SQLAlchemy's declarative base. The redefinition is identical — the warning is noise.
warnings.filterwarnings(
    "ignore",
    message=r"This declarative base already contains a class with the same class name.*",
    category=SAWarning,
)

# yfinance: progress prints are killed at call sites (progress=False). FutureWarning churn
# ("auto_adjust default changed", etc.) routes to /dev/null since we already pass auto_adjust
# explicitly — log capture below catches the rest.
warnings.filterwarnings("ignore", category=FutureWarning, module=r"yfinance.*")


def _has_marked_handler(logger: logging.Logger) -> bool:
    return any(getattr(h, LOG_HANDLER_MARKER, False) for h in logger.handlers)


def _mark(handler: logging.Handler) -> logging.Handler:
    setattr(handler, LOG_HANDLER_MARKER, True)
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
    formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)

    # Console handler (WARNING+ only to avoid cluttering Streamlit)
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.WARNING)
    console.setFormatter(formatter)
    root.addHandler(_mark(console))

    # app.log — catches everything
    app_handler = RotatingFileHandler(LOGS_DIR / LOG_FILES["app"], maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT)
    app_handler.setLevel(level)
    app_handler.setFormatter(formatter)
    root.addHandler(_mark(app_handler))

    # data.log — data fetchers and store
    data_handler = RotatingFileHandler(
        LOGS_DIR / LOG_FILES["data"], maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT
    )
    data_handler.setLevel(logging.DEBUG)
    data_handler.setFormatter(formatter)
    for namespace in ("data.fetchers", "data.store"):
        ns_logger = logging.getLogger(namespace)
        if not _has_marked_handler(ns_logger):
            ns_logger.addHandler(_mark(data_handler))

    # Third-party fetchers — route their loggers into data.log too, but keep them off the
    # console so they don't drown out app messages. propagate=False stops them from also
    # bubbling up to the root handler (console + app.log).
    for namespace in ("yfinance", "peewee"):  # peewee is jugaad-data's underlying ORM
        ns_logger = logging.getLogger(namespace)
        ns_logger.setLevel(logging.INFO)
        ns_logger.propagate = False
        if not _has_marked_handler(ns_logger):
            ns_logger.addHandler(_mark(data_handler))

    # ui.log — UI components and views
    ui_handler = RotatingFileHandler(LOGS_DIR / LOG_FILES["ui"], maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT)
    ui_handler.setLevel(logging.DEBUG)
    ui_handler.setFormatter(formatter)
    ui_logger = logging.getLogger("ui")
    if not _has_marked_handler(ui_logger):
        ui_logger.addHandler(_mark(ui_handler))

    # perf.log — startup/page timing from core.timing.timed()
    perf_handler = RotatingFileHandler(
        LOGS_DIR / LOG_FILES["perf"], maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT
    )
    perf_handler.setLevel(logging.DEBUG)
    perf_handler.setFormatter(formatter)
    perf_logger = logging.getLogger("perf")
    perf_logger.setLevel(logging.DEBUG)
    perf_logger.propagate = False  # don't double-log into app.log
    if not _has_marked_handler(perf_logger):
        perf_logger.addHandler(_mark(perf_handler))


def get_logger(name: str) -> logging.Logger:
    """Get a logger. Call setup_logging() first."""
    return logging.getLogger(name)
