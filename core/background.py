"""Tiny in-process background-task registry — run work off the Streamlit thread and poll status.

Enables stale-while-revalidate: kick off a refresh, keep showing cached data, and swap to fresh
once the task finishes. Tasks are keyed; starting a key that's already running is a no-op, so a
button mash or a rerun won't spawn duplicates. State is per-process (module-level), thread-safe.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

_LOCK = threading.Lock()

# Per-task rolling log depth. Deep enough to follow a long fund refresh, bounded so a 5K-scheme
# run can't grow without limit in a process that may stay up for days.
LOG_CAPACITY = 400


@dataclass
class TaskState:
    status: str = "idle"  # idle | running | done | failed
    started_at: float | None = None
    finished_at: float | None = None
    result: Any = None
    error: str | None = None
    meta: dict = field(default_factory=dict)
    log: deque[tuple[float, str]] = field(default_factory=lambda: deque(maxlen=LOG_CAPACITY))


_TASKS: dict[str, TaskState] = {}


def start_task(key: str, fn: Callable[[], Any], *, meta: dict | None = None) -> bool:
    """Run `fn()` on a daemon thread tracked under `key`. Returns False if already running."""
    with _LOCK:
        existing = _TASKS.get(key)
        if existing and existing.status == "running":
            return False
        _TASKS[key] = TaskState(status="running", started_at=time.time(), meta=meta or {})

    def _run() -> None:
        try:
            res = fn()
            with _LOCK:
                _TASKS[key].status = "done"
                _TASKS[key].finished_at = time.time()
                _TASKS[key].result = res
        except Exception as exc:
            with _LOCK:
                _TASKS[key].status = "failed"
                _TASKS[key].finished_at = time.time()
                _TASKS[key].error = str(exc)

    threading.Thread(target=_run, name=f"task-{key}", daemon=True).start()
    return True


def task_state(key: str) -> TaskState:
    """Current state for `key` (idle if never started).

    Returns a shallow copy (with its own `meta` dict) so a caller reading `.meta` can't race a
    worker thread mutating it via `set_task_progress`.
    """
    with _LOCK:
        state = _TASKS.get(key)
        if state is None:
            return TaskState()
        return replace(state, meta=dict(state.meta), log=deque(state.log, maxlen=LOG_CAPACITY))


def is_running(key: str) -> bool:
    return task_state(key).status == "running"


def set_task_progress(key: str, **fields: Any) -> None:
    """Merge live progress fields into a running task's `meta` (e.g. phase='NAV', done=12, total=99)
    so a polling UI can show how far along a long background job is."""
    with _LOCK:
        state = _TASKS.get(key)
        if state and state.status == "running":
            state.meta.update(fields)


def append_task_log(key: str, line: str) -> None:
    """Append one timestamped line to a running task's rolling log (oldest dropped past
    `LOG_CAPACITY`). Lines are what the task did — one per fund fetched, phase transitions,
    failures — so the UI can show the run as it happens rather than only a progress bar."""
    with _LOCK:
        state = _TASKS.get(key)
        if state and state.status == "running":
            state.log.append((time.time(), line))


def task_log(key: str) -> list[tuple[float, str]]:
    """Snapshot of a task's log as (epoch seconds, line), oldest first."""
    with _LOCK:
        state = _TASKS.get(key)
        return list(state.log) if state else []


def consume_if_finished(key: str) -> TaskState | None:
    """If the task is done/failed, return its state and reset it to idle (one-shot). Else None."""
    with _LOCK:
        state = _TASKS.get(key)
        if state and state.status in ("done", "failed"):
            _TASKS[key] = TaskState()
            return state
    return None
