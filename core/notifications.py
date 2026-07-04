"""Process-wide, Streamlit-free notice bus.

Producers deep in the data/fetcher layer (which must not import Streamlit) push short user-
facing notices here; the UI layer drains them once per run and renders them (as st.toast).
Bounded + thread-safe so background/thread fetches are safe and a headless run can't leak.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass

_MAXLEN = 50
_LOCK = threading.Lock()


@dataclass(frozen=True)
class Notice:
    message: str
    level: str = "warning"  # info | success | warning | error
    key: str | None = None  # de-dup marker; a new notice with the same key replaces the old


_NOTICES: deque[Notice] = deque(maxlen=_MAXLEN)


def push_notice(message: str, *, level: str = "warning", key: str | None = None) -> None:
    """Queue a notice for the UI to surface. If `key` is set, any pending notice with the
    same key is replaced so repeated failures of one symbol collapse to a single toast."""
    notice = Notice(message=message, level=level, key=key)
    with _LOCK:
        if key is not None:
            kept = [n for n in _NOTICES if n.key != key]
            _NOTICES.clear()
            _NOTICES.extend(kept)
        _NOTICES.append(notice)


def drain_notices() -> list[Notice]:
    """Return and clear all pending notices (so each shows exactly once)."""
    with _LOCK:
        items = list(_NOTICES)
        _NOTICES.clear()
    return items
