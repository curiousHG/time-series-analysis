"""Settings page — operational control center for data and system maintenance."""

from __future__ import annotations

import polars as pl
import streamlit as st

from data.repositories.amfi import get_scheme_count
from data.repositories.tradebook import get_tradebook_stats
from services.db_stats import get_db_stats
from services.registry_service import list_tracked
from ui.components.chrome import page_header
from ui.views.settings import amfi, data_sources, db_stats, metrics_cache, refresh, stocks, tradebook


def _render_header() -> None:
    page_header("Settings", "Portfolio inputs, fund data, refresh jobs and database health")


def _render_overview() -> None:
    trade_stats = get_tradebook_stats()
    amfi_count = get_scheme_count()
    tracked = list_tracked()
    db = get_db_stats()

    total_trades = trade_stats.get("total_trades", 0)
    symbols = trade_stats.get("symbols", 0)
    tracked_count = tracked.height
    pending_sources = 0
    unavailable_sources = 0
    if tracked_count:
        # Count pending/unavailable across all three status columns in one pass.
        status_cols = ("navStatus", "holdingsStatus", "metadataStatus")
        counts = tracked.select(
            *[(pl.col(c) == "pending").sum().alias(f"p_{c}") for c in status_cols],
            *[(pl.col(c) == "unavailable").sum().alias(f"u_{c}") for c in status_cols],
        ).row(0)
        pending_sources = sum(counts[: len(status_cols)])
        unavailable_sources = sum(counts[len(status_cols) :])

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Tradebook", f"{total_trades:,}", help=f"{symbols:,} unique trade symbols")
    col2.metric("AMFI Universe", f"{amfi_count:,}", help="Schemes available for search and ISIN matching")
    col3.metric(
        "Tracked Funds",
        f"{tracked_count:,}",
        help=f"{pending_sources} pending source(s), {unavailable_sources} unavailable source(s)",
    )
    col4.metric("Database", db.db_pretty, help=f"{db.table_count} public table(s)")


def _render_tabs() -> None:
    portfolio_tab, fund_tab, stock_tab, system_tab, reference_tab = st.tabs(
        ["Portfolio Data", "Fund Data", "Stock Data", "System", "Reference"]
    )

    with portfolio_tab:
        st.caption("Upload and inspect the Kite/Zerodha tradebook used by Portfolio analytics.")
        tradebook.render()

    with fund_tab:
        st.caption("Sync the AMFI universe, refresh tracked funds, and rebuild derived metrics.")
        amfi.render()
        refresh.render()
        metrics_cache.render()

    with stock_tab:
        st.caption("Retry price downloads for stocks with no fetchable history.")
        stocks.render()

    with system_tab:
        st.caption("Inspect database footprint and table-level storage.")
        db_stats.render()

    with reference_tab:
        st.caption("Source-to-table mappings and schema notes for maintenance work.")
        data_sources.render()


_render_header()
_render_overview()
st.divider()
_render_tabs()
