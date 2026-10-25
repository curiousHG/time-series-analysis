"""Centralized logging setup. Import and call setup_logging() once at app start."""

import logging
import sys
import warnings
from logging.handlers import RotatingFileHandler

from sqlalchemy.exc import SAWarning

from core.constants import (
    LOG_BACKUP_COUNT,
    LOG_CONSOLE_ENABLED,
    LOG_DATE_FORMAT,
    LOG_FILES,
    LOG_FORMAT,
    LOG_HANDLER_MARKER,
    LOG_LEVEL,
    LOG_MAX_BYTES,
    LOGS_DIR,
    PERF_LOG_ENABLED,
)

warnings.filterwarnings(
    "ignore",
    message=r"This declarative base already contains a class with the same class name.*",
    category=SAWarning,
)

warnings.filterwarnings("ignore", category=FutureWarning, module=r"yfinance.*")


def _has_marked_handler(logger: logging.Logger) -> bool:
    return any(getattr(h, LOG_HANDLER_MARKER, False) for h in logger.handlers)


def _mark(handler: logging.Handler) -> logging.Handler:
    setattr(handler, LOG_HANDLER_MARKER, True)
    return handler


def setup_logging(level: int | None = None, *, perf: bool | None = None):
    """Configure rotating file handlers; idempotent across Streamlit hot reloads.

    `level` defaults to LOG_LEVEL and `perf` to PERF_LOG; explicit arguments win. Nothing reaches the terminal
    unless LOG_CONSOLE=1.
    """
    level = logging.getLevelName(LOG_LEVEL) if level is None else level
    if not isinstance(level, int):
        level = logging.INFO
    perf = PERF_LOG_ENABLED if perf is None else perf

    root = logging.getLogger()
    if _has_marked_handler(root):
        return
    root.setLevel(level)
    logging.captureWarnings(True)

    LOGS_DIR.mkdir(exist_ok=True)
    formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)

    if LOG_CONSOLE_ENABLED:
        console = logging.StreamHandler(sys.stderr)
        console.setLevel(logging.WARNING)
        console.setFormatter(formatter)
        root.addHandler(_mark(console))

    app_handler = RotatingFileHandler(LOGS_DIR / LOG_FILES["app"], maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT)
    app_handler.setLevel(level)
    app_handler.setFormatter(formatter)
    root.addHandler(_mark(app_handler))

    data_handler = RotatingFileHandler(
        LOGS_DIR / LOG_FILES["data"], maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT
    )
    data_handler.setLevel(level)
    data_handler.setFormatter(formatter)
    data_logger = logging.getLogger("data")
    if not _has_marked_handler(data_logger):
        data_logger.addHandler(_mark(data_handler))

    for namespace in ("yfinance", "peewee"):
        ns_logger = logging.getLogger(namespace)
        ns_logger.setLevel(logging.INFO)
        ns_logger.propagate = False
        if not _has_marked_handler(ns_logger):
            ns_logger.addHandler(_mark(data_handler))

    logging.getLogger("httpx").setLevel(logging.WARNING)

    perf_logger = logging.getLogger("perf")
    perf_logger.propagate = False
    if not perf:
        perf_logger.disabled = True
        return
    perf_logger.disabled = False
    perf_handler = RotatingFileHandler(
        LOGS_DIR / LOG_FILES["perf"], maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT
    )
    perf_handler.setLevel(logging.DEBUG)
    perf_handler.setFormatter(formatter)
    perf_logger.setLevel(logging.DEBUG)
    if not _has_marked_handler(perf_logger):
        perf_logger.addHandler(_mark(perf_handler))
