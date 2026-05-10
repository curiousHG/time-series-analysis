"""Sidebar + inline filter widgets for the MF Screener.

Layout split:
  Sidebar  — heavy / persistent filters: search, AMC, Category, Plan, Option, AUM / TER
             thresholds, optional risk sliders.
  Inline   — column-visibility multiselect (lives above the table since adjusting column
             density is a more frequent action than touching the sidebar filters).

Pure presentation: returns a `FilterState` that the page orchestrator forwards to
`services.screener_service.apply_filters`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import polars as pl
import streamlit as st

from mutual_funds.metric_catalog import ALL_METRIC_COLS, DEFAULT_VISIBLE_METRICS
from ui.components.aggrid_theme import streamlit_dark_aggrid_theme


@dataclass
class FilterState:
    """Resolved filter selections for one render of the screener page."""

    name_query: str
    amcs: list[str]
    cats: list[str]
    plans: list[str]
    options: list[str]
    aum_min: float
    ter_max: float
    only_untracked: bool
    has_nav: bool
    cagr_min: float | None
    sharpe_min: float | None
    dd_min: float | None
    visible_metrics: list[str]
    aggrid_theme: Any  # str or StAggridTheme


def render_sidebar(df: pl.DataFrame) -> FilterState:
    """Render the sidebar (heavy filters) + the inline column-visibility multiselect."""
    amc_options = sorted(df["fund_house"].drop_nulls().unique().to_list())
    cat_options = sorted(df["category"].drop_nulls().unique().to_list())

    with st.sidebar:
        st.header("Filters")

        # Section 1 — Search & classification (search, AMC, Category, Plan, Option).
        with st.container(border=True):
            name_query = st.text_input(
                "Search by name",
                placeholder="e.g. parag parikh flexi",
                help="Multi-token AND substring (case-insensitive).",
                key="screener_name_query",
            )
            amcs = st.multiselect("AMC", amc_options, key="screener_amcs")
            cats = st.multiselect("Category", cat_options, key="screener_cats")
            plans = st.multiselect("Plan", ["Direct", "Regular"], default=["Direct"], key="screener_plans")
            options = st.multiselect(
                "Option",
                ["Growth", "IDCW", "Bonus", "ETF", "Other"],
                default=["Growth"],
                key="screener_options",
            )

        # Section 2 — Numeric thresholds + risk sliders (gated by Has-NAV).
        with st.container(border=True):
            n1, n2 = st.columns(2)
            aum_min = n1.number_input("Min AUM (₹ Cr)", min_value=0, value=0, step=100, key="screener_aum_min")
            ter_max = n2.number_input(
                "Max TER %", min_value=0.0, value=2.5, step=0.05, format="%.2f", key="screener_ter_max"
            )
            only_untracked = st.checkbox(
                "Only untracked schemes",
                key="screener_only_untracked",
                help="Show only schemes that are NOT in the tracked registry (mf_registry).",
            )
            has_nav = st.checkbox("Has NAV history (enables risk sliders)", key="screener_has_nav")
            cagr_min = sharpe_min = dd_min = None
            if has_nav:
                cagr_min = st.slider("Min 1Y CAGR %", -50, 100, -50, key="screener_cagr_min")
                sharpe_min = st.slider("Min Sharpe", -2.0, 4.0, -2.0, step=0.1, key="screener_sharpe_min")
                dd_min = st.slider("Max drawdown ≥ (%)", -100, 0, -100, key="screener_dd_min")

        st.caption(
            "Every column in the table also has its own header filter (funnel icon → contains "
            "/ numeric range). Use those for ad-hoc slicing without touching the sidebar."
        )

    # Inline — column visibility multiselect lives above the table since adjusting which
    # columns are shown is a frequent action while scanning the data.
    visible_metrics = st.multiselect(
        "Visible metrics",
        options=list(ALL_METRIC_COLS),
        default=list(DEFAULT_VISIBLE_METRICS),
        key="screener_visible_metrics",
        help="Pick the columns to display. Identity columns (Scheme, Category) are always shown.",
    )

    # Theme is fixed to the custom Streamlit-dark variant — the bare AgGrid built-ins didn't
    # render dark on Streamlit, so a toggle would be misleading.
    aggrid_theme = streamlit_dark_aggrid_theme()

    return FilterState(
        name_query=name_query.strip() if name_query else "",
        amcs=amcs,
        cats=cats,
        plans=plans,
        options=options,
        aum_min=aum_min,
        ter_max=ter_max,
        only_untracked=only_untracked,
        has_nav=has_nav,
        cagr_min=cagr_min,
        sharpe_min=sharpe_min,
        dd_min=dd_min,
        visible_metrics=visible_metrics,
        aggrid_theme=aggrid_theme,
    )
