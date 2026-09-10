"""Runs view: the new-run composer, the run grid and its toolbar.

The grid re-renders itself every three seconds while a run is executing (and only then), so a
launched run fills in its progress and headline metrics without the page being touched. Launching is
capped at two concurrent runs — one when an ML strategy or an optimisation is in the mix, because
those retrain a model per walk-forward window and would otherwise starve each other.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

import pandas as pd
import streamlit as st

from core.background import task_state
from services.backtest.config import UniverseSpec
from services.backtest.costs import PRESET_LABELS
from ui.components.background_refresh import BackgroundRefresh, BackgroundRefreshGroup, progress_cb
from ui.constants import BACKTEST_FILTER_DEFAULTS, BACKTEST_PERSIST_KEY
from ui.state.filter_persistence import hydrate_filters, make_persist_callback
from ui.state.loaders import clear_backtest_caches, load_backtest_runs_cached
from ui.views.backtest_lab import run_form, state
from ui.views.backtest_lab.params import params_summary

if TYPE_CHECKING:
    import polars as pl

    from services.backtest.runs import StrategySpec

logger = logging.getLogger(__name__)

MAX_CONCURRENT = 2
HEAVY_CONCURRENT = 1
GRID_KEY = "bt_run_grid"
EDIT_OPEN_KEY = "bt_edit_open"
POLL_EVERY = "3s"
ACTIVE_STATUSES = ("queued", "running")
STATUS_OPTIONS = ("draft", "queued", "running", "done", "failed")
_persist_filters = make_persist_callback(BACKTEST_PERSIST_KEY, BACKTEST_FILTER_DEFAULTS)


def _task_key(run_id: int) -> str:
    from services.backtest.runs import task_key  # noqa: PLC0415 — pulls the engine + quantstats in

    return task_key(run_id)


def _opt_task_key(run_id: int) -> str:
    from services.backtest.runs import optimization_task_key  # noqa: PLC0415

    return optimization_task_key(run_id)


def _summarize(result: Any) -> str:
    metrics = result or {}
    return f"{metrics.get('total_trades', 0)} trades · final ₹{metrics.get('final_equity', 0):,.0f}"


def refresh_for(run_id: int, name: str) -> BackgroundRefresh:
    """The background task handle for one run — one per run id, keyed `bt_run_<id>`."""
    return BackgroundRefresh(
        _task_key(run_id), f"Run “{name}”", cache_clearers=(clear_backtest_caches,), summarize=_summarize
    )


def universe_label(payload: dict | None) -> str:
    return UniverseSpec.from_dict(payload).label() if payload else "—"


def _progress(run_id: int) -> str:
    meta = task_state(_task_key(run_id)).meta
    total = meta.get("total")
    phase = meta.get("phase", "running")
    return f"{phase} {meta.get('done', 0)}/{total}" if total else str(phase)


def _status_cell(record: dict) -> str:
    status = record["status"]
    return _progress(record["id"]) if status in ACTIVE_STATUSES else status


def _payload(value) -> dict:
    """The repository hands JSON columns back as strings so the cached frame stays picklable."""
    if not value:
        return {}
    return json.loads(value) if isinstance(value, str) else dict(value)


def records(runs: pl.DataFrame) -> list[dict]:
    """Run rows as plain dicts — `list_runs` carries JSON columns polars keeps as objects."""
    return [] if runs.is_empty() else list(runs.iter_rows(named=True))


def display_frame(rows: list[dict]) -> pd.DataFrame:
    """The grid's columns, in the order they are shown."""
    return pd.DataFrame(
        [
            {
                "Name": r["name"],
                "Strategy": r["strategy"],
                "Universe": universe_label(_payload(r["universe"])),
                "Params": params_summary(_payload(r["params"])),
                "Costs": PRESET_LABELS.get(_payload(r["costs"]).get("name"), "Custom"),
                "Period": f"{r['start']:%b %Y} to {r['end']:%b %Y}",
                "Status": _status_cell(r),
                "CAGR %": _payload(r["summary"]).get("cagr"),
                "Sharpe": _payload(r["summary"]).get("sharpe"),
                "Max DD %": _payload(r["summary"]).get("max_drawdown"),
                "Win %": _payload(r["summary"]).get("win_rate"),
                "Trades": _payload(r["summary"]).get("total_trades"),
                "Net P&L": _payload(r["summary"]).get("total_profit_abs"),
                "Ran": r.get("finished_at"),
            }
            for r in rows
        ]
    )


_COLUMN_CONFIG = {
    "Name": st.column_config.TextColumn(width="medium"),
    "Params": st.column_config.TextColumn(width="medium"),
    "CAGR %": st.column_config.NumberColumn(format="%.1f%%"),
    "Sharpe": st.column_config.NumberColumn(format="%.2f"),
    "Max DD %": st.column_config.NumberColumn(format="%.1f%%"),
    "Win %": st.column_config.NumberColumn(format="%.0f%%"),
    "Trades": st.column_config.NumberColumn(format="%d"),
    "Net P&L": st.column_config.NumberColumn(format="₹%.0f"),
    "Ran": st.column_config.DatetimeColumn(format="DD MMM YYYY, HH:mm"),
}


def filter_records(rows: list[dict]) -> list[dict]:
    """Apply the persisted name / status / strategy filters."""
    query = (st.session_state.get("bt_filter_name") or "").strip().lower()
    statuses = st.session_state.get("bt_filter_status") or []
    strategies = st.session_state.get("bt_filter_strategy") or []
    return [
        r
        for r in rows
        if (not query or query in r["name"].lower())
        and (not statuses or r["status"] in statuses)
        and (not strategies or r["strategy"] in strategies)
    ]


def _render_filters(rows: list[dict]) -> None:
    left, middle, right = st.columns([2, 1, 1])
    with left:
        st.text_input("Search runs", key="bt_filter_name", placeholder="Filter by name", on_change=_persist_filters)
    with middle:
        st.multiselect("Status", STATUS_OPTIONS, key="bt_filter_status", on_change=_persist_filters)
    with right:
        st.multiselect(
            "Strategy", sorted({r["strategy"] for r in rows}), key="bt_filter_strategy", on_change=_persist_filters
        )


def launch(run_ids: list[int], rows: list[dict], catalog: dict[str, StrategySpec]) -> list[int]:
    """Start as many of `run_ids` as the concurrency cap allows; returns the ids actually started."""
    from services.backtest.runs import start_run  # noqa: PLC0415 — pulls the engine + quantstats in

    by_id = {r["id"]: r for r in rows}
    running = [r["id"] for r in rows if refresh_for(r["id"], r["name"]).is_running()]
    heavy = any(
        catalog[r["strategy"]].is_ml for r in rows if r["id"] in {*running, *run_ids} and r["strategy"] in catalog
    )
    heavy = heavy or any(task_state(_opt_task_key(r["id"])).status == "running" for r in rows)
    slots = max((HEAVY_CONCURRENT if heavy else MAX_CONCURRENT) - len(running), 0)
    started: list[int] = []
    for run_id in run_ids[:slots]:
        name = by_id.get(run_id, {}).get("name", str(run_id))
        handle = refresh_for(run_id, name)
        key = handle.key
        if handle.start(lambda rid=run_id, k=key: start_run(rid, progress_cb=progress_cb(k))):
            started.append(run_id)
    queued_out = len(run_ids) - len(started)
    if queued_out > 0:
        st.toast(f"{queued_out} run(s) not started — {slots} slot(s) free right now.", icon="⏳")
    if started:
        logger.info("backtest lab: launched run(s) %s", started)
    return started


def _delete(run_ids: list[int]) -> None:
    from services.backtest.runs import delete_run  # noqa: PLC0415 — pulls the engine + quantstats in

    for run_id in run_ids:
        delete_run(run_id)
    clear_backtest_caches()
    if state.selected_run() in run_ids:
        state.set_selected_run(None)
    logger.info("backtest lab: deleted run(s) %s", run_ids)


def _duplicate(run_ids: list[int]) -> int | None:
    from services.backtest.runs import duplicate_run  # noqa: PLC0415 — pulls the engine + quantstats in

    new_id = None
    for run_id in run_ids:
        new_id = duplicate_run(run_id, {})
    clear_backtest_caches()
    return new_id


def _toolbar(selected: list[int], rows: list[dict], catalog: dict[str, StrategySpec]) -> None:
    one, several = len(selected) == 1, state.MIN_COMPARE <= len(selected) <= state.MAX_COMPARE
    run_col, dup_col, edit_col, open_col, cmp_col, del_col = st.columns(6)
    if run_col.button("Run", key="bt_toolbar_run", type="primary", disabled=not selected, use_container_width=True):
        launch(selected, rows, catalog)
        st.rerun(scope="app")
    if dup_col.button("Duplicate", key="bt_toolbar_dup", disabled=not selected, use_container_width=True):
        new_id = _duplicate(selected)
        if new_id is not None:
            state.set_selected_run(new_id)
        st.rerun(scope="app")
    if edit_col.button("Edit", key="bt_toolbar_edit", disabled=not one, use_container_width=True):
        st.session_state[EDIT_OPEN_KEY] = selected[0]
        st.rerun(scope="app")
    if open_col.button("Open", key="bt_toolbar_open", disabled=not one, use_container_width=True):
        state.go_to("Detail", run_id=selected[0])
    if cmp_col.button("Compare", key="bt_toolbar_compare", disabled=not several, use_container_width=True):
        state.go_to("Compare", compare=selected)
    with del_col.popover("Delete", disabled=not selected, use_container_width=True):
        st.caption(f"Delete {len(selected)} run(s) and every trade, optimisation and trial under them?")
        if st.button("Delete permanently", key="bt_toolbar_delete_confirm", type="primary"):
            _delete(selected)
            st.rerun(scope="app")


def _grid(rows: list[dict], catalog: dict[str, StrategySpec], group: BackgroundRefreshGroup) -> None:
    event = st.dataframe(
        display_frame(rows),
        key=GRID_KEY,
        selection_mode="multi-row",
        on_select="rerun",
        hide_index=True,
        use_container_width=True,
        column_config=_COLUMN_CONFIG,
    )
    picked = [rows[i]["id"] for i in event.selection["rows"] if i < len(rows)]
    _toolbar(picked, rows, catalog)
    if group.any_running():
        group.poll(compact=True)


def render(catalog: dict[str, StrategySpec]) -> None:
    hydrate_filters(BACKTEST_PERSIST_KEY, BACKTEST_FILTER_DEFAULTS)
    rows = records(load_backtest_runs_cached())
    group = BackgroundRefreshGroup([refresh_for(r["id"], r["name"]) for r in rows], poll_every=POLL_EVERY)
    group.consume()

    with st.expander("New run", icon=":material/add:", expanded=not rows):
        run_form.render_new_run(catalog, launch=lambda ids: launch(ids, rows, catalog))

    if not rows:
        st.info("No runs yet — compose one above and press **Create and run**.")
        return

    _render_filters(rows)
    visible = filter_records(rows)
    if not visible:
        st.caption("No run matches these filters.")
        return
    grid = st.fragment(run_every=POLL_EVERY if group.any_running() else None)(_grid)
    grid(visible, catalog, group)

    open_for = st.session_state.pop(EDIT_OPEN_KEY, None)
    if open_for is not None:
        run_form.edit_dialog(int(open_for), catalog)
