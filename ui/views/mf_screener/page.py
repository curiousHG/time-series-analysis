"""MF Screener — Streamlit page entry.

Orchestrates the four UI sections (sidebar filters, AgGrid table, risk-vs-return chart,
inline add-to-tracked controls) on top of the pure data layer in `services.screener_service`.
The page itself stays small — every render concern is delegated to a sibling module so
the responsibilities are obvious and the page is easy to scan.
"""

from __future__ import annotations

import polars as pl
import streamlit as st

from data.repositories.amfi import get_scheme_count
from services.registry_service import list_tracked
from services.screener_service import apply_filters
from ui.state.loaders import load_screener_df_cached
from ui.views.mf_screener.backfill import render_inline_backfill
from ui.views.mf_screener.chart import render_risk_return_chart
from ui.views.mf_screener.filters import render_sidebar
from ui.views.mf_screener.table import render_open_action, render_selection_echo, render_table


def _render_universe_summary(amfi_count: int, filtered: pl.DataFrame) -> None:
    """Top-of-page header: AMFI universe + Tracked metrics, with the add-to-tracked
    controls (Top-N input + Fetch button with hover-help) inline to the right of the
    Tracked metric. Per-source availability breakdown follows underneath.
    """
    tracked_df = list_tracked()
    tracked_count = tracked_df.height

    nav_count = metadata_count = holdings_count = 0
    if tracked_count:
        nav_count = tracked_df.filter(pl.col("navStatus") == "available").height
        metadata_count = tracked_df.filter(pl.col("metadataStatus") == "available").height
        holdings_count = tracked_df.filter(pl.col("holdingsStatus") == "available").height

    m1, m2, m3, m4 = st.columns([1, 1, 1, 2], vertical_alignment="bottom")
    m1.metric("AMFI universe", f"{amfi_count:,}")
    m2.metric("Tracked", f"{tracked_count:,}")
    render_inline_backfill(filtered, m3, m4)

    if tracked_count:
        st.caption(
            f"Available data per source — "
            f"NAV: **{nav_count:,}** · "
            f"Metadata: **{metadata_count:,}** · "
            f"Holdings: **{holdings_count:,}** "
            f"(out of {tracked_count:,} tracked)"
        )


st.title("Mutual Fund Screener")

_amfi_count = get_scheme_count()
if _amfi_count == 0:
    st.warning("AMFI master data not loaded. Run **Sync AMFI Master** from Settings first.")
    st.stop()

_df = load_screener_df_cached()

# Sidebar + inline filters → resolved FilterState.
_state = render_sidebar(_df)

_filtered = apply_filters(
    _df,
    name_query=_state.name_query,
    amcs=_state.amcs,
    cats=_state.cats,
    plans=_state.plans,
    options=_state.options,
    aum_min=_state.aum_min,
    ter_max=_state.ter_max,
    only_tracked=False,  # filter retired — see ui.views.mf_screener.filters
    only_untracked=_state.only_untracked,
    has_nav=_state.has_nav,
    cagr_min=_state.cagr_min,
    sharpe_min=_state.sharpe_min,
    dd_min=_state.dd_min,
)

# Header + inline add-to-tracked (needs `_filtered` for the Top-N picker).
_render_universe_summary(_amfi_count, _filtered)
st.caption(f"{_filtered.height:,} of {_df.height:,} schemes match · **click a fund name** to open it in MF Analysis")

# Table → grid response → open-fund action + selection-echo expander.
_, _grid_response = render_table(_filtered, _state.visible_metrics, _state.aggrid_theme)
render_open_action(_grid_response)
render_selection_echo(_grid_response)

# Risk-vs-return scatter for the filtered universe.
render_risk_return_chart(_filtered)
