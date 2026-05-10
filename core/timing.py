"""Lightweight timing helpers — context manager + decorator that log durations to logs/perf.log.

Usage:
    from core.timing import timed, timeit

    with timed("init_schema"):
        init_schema()

    @timeit("load_amfi_df")
    def load_amfi_df(...):
        ...

A `slow_threshold_ms` (default 100) demotes very fast calls to DEBUG so the file isn't drowned
in microsecond-level noise — anything above the threshold logs at INFO and is easy to grep.
"""

from __future__ import annotations

import functools
import logging
import time
from collections.abc import Callable
from contextlib import contextmanager
from typing import TypeVar

logger = logging.getLogger("perf")

T = TypeVar("T")

DEFAULT_SLOW_MS = 100.0


@contextmanager
def timed(label: str, *, slow_threshold_ms: float = DEFAULT_SLOW_MS):
    """Time a block of code and emit one log line on exit.

    Logs at INFO when duration >= slow_threshold_ms, DEBUG otherwise. Always logs on error.
    """
    t0 = time.perf_counter()
    try:
        yield
    except Exception:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        logger.exception("[%s] FAILED after %.1fms", label, elapsed_ms)
        raise
    else:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        level = logging.INFO if elapsed_ms >= slow_threshold_ms else logging.DEBUG
        logger.log(level, "[%s] %.1fms", label, elapsed_ms)


def timeit(label: str | None = None, *, slow_threshold_ms: float = DEFAULT_SLOW_MS) -> Callable:
    """Decorator form of `timed`. Uses the function's qualified name when label is omitted."""

    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        name = label or f"{fn.__module__}.{fn.__qualname__}"

        @functools.wraps(fn)
        def wrapper(*args, **kwargs) -> T:
            with timed(name, slow_threshold_ms=slow_threshold_ms):
                return fn(*args, **kwargs)

        return wrapper

    return decorator
