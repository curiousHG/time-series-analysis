"""Sidebar + inline filter widgets for the MF Screener. Returns a `FilterState`."""

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
    """Seed missing screener_* keys from selections.json before their widgets render.

    Idempotent so live edits aren't clobbered; re-seeds each run since Streamlit GCs the
    keys on page nav. Widgets that read a seeded key must omit `default=`/`value=`.
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
    sub_cats: list[str]
    plans: list[str]
    options: list[str]
    aum_min: float
    ter_max: float
    min_age_years: float
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
    # Sub-category options cascade off the selected asset class(es).
    _sel_cats = st.session_state.get("screener_cats") or []
    _sub_source = df.filter(pl.col("category").is_in(_sel_cats)) if _sel_cats else df
    sub_cat_options = sorted(_sub_source["sub_category"].drop_nulls().unique().to_list())
    # Prune any persisted sub-category that the current class selection no longer offers,
    # so the keyed multiselect never gets a value outside its options.
    if "screener_sub_cats" in st.session_state:
        st.session_state["screener_sub_cats"] = [
            s for s in st.session_state["screener_sub_cats"] if s in sub_cat_options
        ]

    with st.sidebar:
        st.header("Filters")

        # Section 1 — Search & classification.
        with st.container(border=True):
            name_query = st.text_input(
                "Search by name",
                placeholder="e.g. parag parikh flexi",
                help="Case-insensitive; all tokens must match.",
                key="screener_name_query",
                on_change=_persist_filters,
            )
            amcs = st.multiselect("AMC", amc_options, key="screener_amcs", on_change=_persist_filters)
            cats = st.multiselect("Category", cat_options, key="screener_cats", on_change=_persist_filters)
            sub_cats = st.multiselect(
                "Sub-category", sub_cat_options, key="screener_sub_cats", on_change=_persist_filters
            )
            plans = st.multiselect("Plan", ["Direct", "Regular"], key="screener_plans", on_change=_persist_filters)
            options = st.multiselect(
                "Option",
                ["Growth", "IDCW", "Bonus", "ETF", "Other"],
                key="screener_options",
                on_change=_persist_filters,
            )

        # Section 2 — Numeric thresholds + risk sliders (gated by Has NAV).
        with st.container(border=True):
            n1, n2 = st.columns(2)
            aum_min = n1.number_input(
                "Min AUM (₹ Cr)", min_value=0, step=100, key="screener_aum_min", on_change=_persist_filters
            )
            ter_max = n2.number_input(
                "Max TER %", min_value=0.0, step=0.05, format="%.2f", key="screener_ter_max", on_change=_persist_filters
            )
            min_age_years = st.slider(
                "Min fund age (years)",
                0.0,
                25.0,
                step=0.5,
                key="screener_min_age",
                on_change=_persist_filters,
                help="Keep funds with at least this much NAV history; funds without metrics drop out when > 0.",
            )
            only_untracked = st.checkbox(
                "Only untracked schemes",
                key="screener_only_untracked",
                help="Show only schemes not in the tracked registry.",
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

        st.caption("Each table column also has its own header filter for ad-hoc slicing.")

    # Column-visibility multiselect sits above the table — toggled often while scanning.
    visible_metrics = st.multiselect(
        "Visible metrics",
        options=list(ALL_METRIC_COLS),
        key="screener_visible_metrics",
        on_change=_persist_filters,
        help="Columns to display; Scheme and Category are always shown.",
    )

    # Fixed to the custom Streamlit-dark variant; AgGrid's built-ins don't render dark here.
    aggrid_theme = streamlit_dark_aggrid_theme()

    return FilterState(
        name_query=name_query.strip() if name_query else "",
        amcs=amcs,
        cats=cats,
        sub_cats=sub_cats,
        plans=plans,
        options=options,
        aum_min=aum_min,
        ter_max=ter_max,
        min_age_years=min_age_years,
        only_untracked=only_untracked,
        has_nav=has_nav,
        cagr_min=cagr_min,
        sharpe_min=sharpe_min,
        dd_min=dd_min,
        visible_metrics=visible_metrics,
        aggrid_theme=aggrid_theme,
    )
