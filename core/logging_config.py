"""Centralized logging setup. Import and call setup_logging() once at app start."""

import logging
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler

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

_initialized = False


def setup_logging(level: int = logging.INFO):
    """Configure logging with rotating file handlers. Safe to call multiple times."""
    global _initialized
    if _initialized:
        return
    _initialized = True

    LOGS_DIR.mkdir(exist_ok=True)

    formatter = logging.Formatter(FMT, datefmt=DATE_FMT)

    # Root logger → app.log + stderr
    root = logging.getLogger()
    root.setLevel(level)

    # Console handler (WARNING+ only to avoid cluttering Streamlit)
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.WARNING)
    console.setFormatter(formatter)
    root.addHandler(console)

    # app.log — catches everything
    app_handler = RotatingFileHandler(
        LOGS_DIR / LOG_FILES["app"], maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT
    )
    app_handler.setLevel(level)
    app_handler.setFormatter(formatter)
    root.addHandler(app_handler)

    # data.log — data fetchers and store
    data_handler = RotatingFileHandler(
        LOGS_DIR / LOG_FILES["data"], maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT
    )
    data_handler.setLevel(logging.DEBUG)
    data_handler.setFormatter(formatter)
    for namespace in ("data.fetchers", "data.store"):
        logging.getLogger(namespace).addHandler(data_handler)

    # ui.log — UI components and views
    ui_handler = RotatingFileHandler(
        LOGS_DIR / LOG_FILES["ui"], maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT
    )
    ui_handler.setLevel(logging.DEBUG)
    ui_handler.setFormatter(formatter)
    logging.getLogger("ui").addHandler(ui_handler)


def get_logger(name: str) -> logging.Logger:
    """Get a logger. Call setup_logging() first."""
    return logging.getLogger(name)
