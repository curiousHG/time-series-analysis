"""Backtest Lab — one page, four views: the run grid, a run's detail, a comparison, the optimiser."""

from __future__ import annotations

import streamlit as st

from ui.components.chrome import page_header
from ui.components.notifications import render_toasts
from ui.state.loaders import load_strategy_catalog_cached
from ui.views.backtest_lab import compare_tab, optimise_tab, run_detail, runs_tab, state

SUBTITLE = "Basket backtests with real Zerodha costs, walk-forward ML and an Optuna optimiser"


def _dispatch(view: str) -> None:
    if view == "Detail":
        run_detail.render()
    elif view == "Compare":
        compare_tab.render()
    elif view == "Optimise":
        optimise_tab.render(load_strategy_catalog_cached())
    else:
        runs_tab.render(load_strategy_catalog_cached())


def _render_page() -> None:
    state.apply_pending()
    page_header("Backtest Lab", SUBTITLE)
    render_toasts()
    view = st.segmented_control("View", state.VIEWS, key=state.VIEW_KEY, label_visibility="collapsed")
    _dispatch(view or state.current_view())


_render_page()
