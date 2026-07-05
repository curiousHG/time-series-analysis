"""Reusable stale-while-revalidate background refresh for Streamlit views.

Wraps `core.background` (start_task / task_state / consume_if_finished / set_task_progress) + a
polling `st.fragment` so any view can: keep showing the last-cached data, run a refresh off-thread,
show live progress, and swap to fresh data + toast the moment it finishes — in ~5 lines.

Usage (single task):

    from ui.components.background_refresh import BackgroundRefresh, progress_cb

    _RF = BackgroundRefresh("stock_data_refresh", "Data refresh", cache_clearers=(_clear_caches,),
                            summarize=lambda r: f"{r['stock_rows']:,} rows")
    def render():
        _RF.consume()                       # 1) swap-to-fresh + toast if a run just finished
        if _RF.is_running():
            _RF.poll()                      # 2) live banner/progress + full-rerun when done
        _RF.start_button("🔄 Refresh all data", lambda: refresh_all_stock_data(
            progress_cb=progress_cb(_RF.key)), type="primary")

For a view driving several tasks, wrap them in a `BackgroundRefreshGroup`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import streamlit as st

from core.background import consume_if_finished, is_running, set_task_progress, start_task, task_state

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence


def progress_cb(key: str) -> Callable[..., None]:
    """Adapter turning a service's `progress_cb(**fields)` into `set_task_progress(key, ...)`."""
    return lambda **fields: set_task_progress(key, **fields)


def _render_status(key: str, label: str) -> None:
    meta = task_state(key).meta
    total = meta.get("total")
    if total:
        phase, done = meta.get("phase", "…"), meta.get("done", 0)
        st.info(f"🔄 {label} running in the background — {phase} [{done}/{total}]. Showing last-cached data meanwhile.")
        st.progress(min(done / total, 1.0))
    else:
        st.info(f"🔄 {label} running in the background — showing the last-cached data; updates when ready.")


@dataclass
class BackgroundRefresh:
    """One background refresh task, keyed by `key`. `summarize(result)` builds the done-toast body;
    `cache_clearers` are invoked once when a run finishes (to drop stale @st.cache_data)."""

    key: str
    label: str
    cache_clearers: Sequence[Callable[[], None]] = field(default_factory=tuple)
    summarize: Callable[[Any], str] = str
    poll_every: str = "3s"

    def is_running(self) -> bool:
        return is_running(self.key)

    def start(self, fn: Callable[[], Any], *, meta: dict | None = None) -> bool:
        """Start the task if not already running (no-op if it is). Returns whether it started."""
        return start_task(self.key, fn, meta=meta or {})

    def start_button(self, text: str, fn: Callable[[], Any], *, meta: dict | None = None, **btn_kwargs: Any) -> bool:
        """Render a start button (disabled while running); on click, start + rerun."""
        btn_kwargs.setdefault("disabled", self.is_running())
        btn_kwargs.setdefault("use_container_width", True)
        if st.button(text, **btn_kwargs):
            self.start(fn, meta=meta)
            st.rerun()
            return True
        return False

    def consume(self) -> None:
        """If a run just finished: clear caches, toast the outcome, and reset the task to idle."""
        finished = consume_if_finished(self.key)
        if finished is None:
            return
        for clear in self.cache_clearers:
            clear()
        if finished.status == "done":
            st.toast(f"{self.label} done — {self.summarize(finished.result)}.", icon="✅")
        else:
            st.toast(f"{self.label} failed: {finished.error}", icon="⚠️")

    def poll(self) -> None:
        """Render the live status banner and poll: full-rerun the page when the task finishes."""
        BackgroundRefreshGroup((self,), poll_every=self.poll_every).poll()


@dataclass
class BackgroundRefreshGroup:
    """Several BackgroundRefresh tasks sharing one poll fragment (e.g. a page with two refreshes)."""

    refreshes: Sequence[BackgroundRefresh]
    poll_every: str = "3s"

    def consume(self) -> None:
        for r in self.refreshes:
            r.consume()

    def any_running(self) -> bool:
        return any(r.is_running() for r in self.refreshes)

    def poll(self) -> None:
        keys_labels = [(r.key, r.label) for r in self.refreshes]

        @st.fragment(run_every=self.poll_every)
        def _poll() -> None:
            running = [(k, lbl) for k, lbl in keys_labels if is_running(k)]
            if not running:
                st.rerun(scope="app")  # a task just finished → rerun the page to consume + show fresh
                return
            for key, label in running:
                _render_status(key, label)

        _poll()
