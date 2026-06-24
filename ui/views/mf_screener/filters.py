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

from mutual_funds.metric_catalog import ALL_METRIC_COLS
from ui.components.aggrid_theme import streamlit_dark_aggrid_theme
from ui.constants import FILTER_DEFAULTS, SCREENER_PERSIST_KEY, SLIDER_DEFAULTS
from ui.persistence.selections import load_selection, save_selection


def _hydrate_filters() -> None:
    """Seed any missing screener_* keys from disk before their widgets are created.

    Runs on every render but is idempotent: a key already present in session_state — a
    default seeded earlier, or a live in-session edit — is left untouched, so edits are
    never clobbered. The reason this can't be a one-time guard: when the user navigates
    to another page (e.g. opening a fund in MF Analysis), Streamlit garbage-collects the
    screener_* widget keys because those widgets aren't rendered there. On return we must
    re-seed them, which we do from selections.json — kept current by `_persist_filters`
    on every change — so the filter state survives navigating away and back.

    Streamlit forbids passing both a widget `default=`/`value=` and a pre-seeded
    session_state key, so the widgets below omit those args for every key we seed here.
    """
    saved = load_selection(SCREENER_PERSIST_KEY, {})
    for key, default in {**FILTER_DEFAULTS, **SLIDER_DEFAULTS}.items():
        if key not in st.session_state:
            st.session_state[key] = saved.get(key, default)


def _persist_filters() -> None:
    """on_change callback: snapshot all screener_* filter values to selections.json."""
    keys = list(FILTER_DEFAULTS) + list(SLIDER_DEFAULTS)
    snapshot = {k: st.session_state[k] for k in keys if k in st.session_state}
    save_selection(SCREENER_PERSIST_KEY, snapshot)


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
    _hydrate_filters()  # seed session_state from selections.json before any widget renders

    amc_options = sorted(df["fund_house"].drop_nulls().unique().to_list())
    cat_options = sorted(df["category"].drop_nulls().unique().to_list())

    with st.sidebar:
        st.header("Filters")

        # Section 1 — Search & classification (search, AMC, Category, Plan, Option).
        # Widgets seeded via session_state (see `_hydrate_filters`) omit `default=`; the
        # shared `on_change` persists every change back to selections.json.
        with st.container(border=True):
            name_query = st.text_input(
                "Search by name",
                placeholder="e.g. parag parikh flexi",
                help="Multi-token AND substring (case-insensitive).",
                key="screener_name_query",
                on_change=_persist_filters,
            )
            amcs = st.multiselect("AMC", amc_options, key="screener_amcs", on_change=_persist_filters)
            cats = st.multiselect("Category", cat_options, key="screener_cats", on_change=_persist_filters)
            plans = st.multiselect("Plan", ["Direct", "Regular"], key="screener_plans", on_change=_persist_filters)
            options = st.multiselect(
                "Option",
                ["Growth", "IDCW", "Bonus", "ETF", "Other"],
                key="screener_options",
                on_change=_persist_filters,
            )

        # Section 2 — Numeric thresholds + risk sliders (gated by Has-NAV).
        with st.container(border=True):
            n1, n2 = st.columns(2)
            aum_min = n1.number_input(
                "Min AUM (₹ Cr)", min_value=0, step=100, key="screener_aum_min", on_change=_persist_filters
            )
            ter_max = n2.number_input(
                "Max TER %", min_value=0.0, step=0.05, format="%.2f", key="screener_ter_max", on_change=_persist_filters
            )
            only_untracked = st.checkbox(
                "Only untracked schemes",
                key="screener_only_untracked",
                help="Show only schemes that are NOT in the tracked registry (mf_registry).",
                on_change=_persist_filters,
            )
            has_nav = st.checkbox(
                "Has NAV history (enables risk sliders)", key="screener_has_nav", on_change=_persist_filters
            )
            cagr_min = sharpe_min = dd_min = None
            if has_nav:
                cagr_min = st.slider("Min 1Y CAGR %", -50, 100, key="screener_cagr_min", on_change=_persist_filters)
                sharpe_min = st.slider(
                    "Min Sharpe", -2.0, 4.0, step=0.1, key="screener_sharpe_min", on_change=_persist_filters
                )
                dd_min = st.slider("Max drawdown ≥ (%)", -100, 0, key="screener_dd_min", on_change=_persist_filters)

        st.caption(
            "Every column in the table also has its own header filter (funnel icon → contains "
            "/ numeric range). Use those for ad-hoc slicing without touching the sidebar."
        )

    # Inline — column visibility multiselect lives above the table since adjusting which
    # columns are shown is a frequent action while scanning the data.
    visible_metrics = st.multiselect(
        "Visible metrics",
        options=list(ALL_METRIC_COLS),
        key="screener_visible_metrics",
        on_change=_persist_filters,
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
