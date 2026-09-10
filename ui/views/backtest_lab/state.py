"""Session-state contract for the Backtest Lab: the visible view, the open run, the compare queue.

The view switcher and the detail run picker are keyed widgets, and a widget key cannot be written
after its widget rendered — so every cross-view jump stages a `*_pending` key that `apply_pending`
applies at the top of the next run (the `sa_ticker_pending` pattern).
"""

from __future__ import annotations

import streamlit as st

from ui.constants import BACKTEST_SELECTION_KEY
from ui.persistence.selections import load_selection, save_selection

VIEWS = ("Runs", "Detail", "Compare", "Optimise")
VIEW_KEY = "bt_view"
VIEW_PENDING = "bt_view_pending"
RUN_KEY = "bt_selected_run"
RUN_PENDING = "bt_run_pending"
DETAIL_PICKER_KEY = "bt_detail_run"
OPT_PICKER_KEY = "bt_opt_run"
PREFILL_KEY = "bt_prefill"
PREFILL_PENDING = "bt_prefill_pending"
COMPARE_KEY = "bt_compare_ids"
MIN_COMPARE = 2
MAX_COMPARE = 5


def apply_pending() -> None:
    """Apply staged view / run / prefill handoffs before any widget instantiates."""
    st.session_state.setdefault(VIEW_KEY, VIEWS[0])
    view = st.session_state.pop(VIEW_PENDING, None)
    if view in VIEWS:
        st.session_state[VIEW_KEY] = view
    run_id = st.session_state.pop(RUN_PENDING, None)
    if run_id is not None:
        set_selected_run(int(run_id))
        st.session_state[DETAIL_PICKER_KEY] = int(run_id)
        st.session_state[OPT_PICKER_KEY] = int(run_id)
    prefill = st.session_state.pop(PREFILL_PENDING, None)
    if prefill is not None:
        st.session_state[PREFILL_KEY] = dict(prefill)


def current_view() -> str:
    return st.session_state.get(VIEW_KEY) or VIEWS[0]


def go_to(view: str, *, run_id: int | None = None, compare: list[int] | None = None) -> None:
    """Stage a view switch (with the run to open, or the runs to compare) and rerun the page."""
    st.session_state[VIEW_PENDING] = view
    if run_id is not None:
        st.session_state[RUN_PENDING] = int(run_id)
    if compare is not None:
        set_compare_ids(compare)
    st.rerun(scope="app")


def selected_run() -> int | None:
    """The run the Detail / Optimise views open on, restored from selections.json on a fresh session."""
    if RUN_KEY not in st.session_state:
        stored = load_selection(BACKTEST_SELECTION_KEY)
        if stored is not None:
            st.session_state[RUN_KEY] = int(stored)
    value = st.session_state.get(RUN_KEY)
    return int(value) if value is not None else None


def set_selected_run(run_id: int | None) -> None:
    """Remember the open run. The write to selections.json is skipped when nothing changed, because
    the Detail view calls this on every rerun."""
    value = None if run_id is None else int(run_id)
    if st.session_state.get(RUN_KEY) == value and RUN_KEY in st.session_state:
        return
    st.session_state[RUN_KEY] = value
    save_selection(BACKTEST_SELECTION_KEY, value)


def compare_ids() -> list[int]:
    return [int(v) for v in st.session_state.get(COMPARE_KEY, [])]


def set_compare_ids(run_ids) -> None:
    st.session_state[COMPARE_KEY] = [int(v) for v in run_ids][:MAX_COMPARE]


def take_prefill() -> dict | None:
    """Consume a config prefill staged by another page (Stock Analysis / Stock Screener)."""
    return st.session_state.pop(PREFILL_KEY, None)


def sync_picker(key: str, options: list[int]) -> int | None:
    """Keep a keyed run picker on a valid option and return it — drops ids that no longer exist."""
    if not options:
        st.session_state.pop(key, None)
        return None
    if st.session_state.get(key) not in options:
        current = selected_run()
        st.session_state[key] = current if current in options else options[0]
    return int(st.session_state[key])
