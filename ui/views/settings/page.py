"""Settings page — operational control center for data and system maintenance."""

from __future__ import annotations

import polars as pl
import streamlit as st

from data.repositories.amfi import get_scheme_count
from data.repositories.tradebook import get_tradebook_stats
from services.db_stats import get_db_stats
from services.registry_service import list_tracked
from ui.views.settings import amfi, data_sources, db_stats, metrics_cache, refresh, tradebook


def _inject_styles() -> None:
    st.markdown(
        """
        <style>
        section[data-testid="stSidebar"] + section .block-container {
            padding-top: 2rem;
        }
        .settings-kicker {
            color: #94a3b8;
            font-size: 0.82rem;
            letter-spacing: 0.08em;
            text-transform: uppercase;
            margin-bottom: 0.15rem;
        }
        .settings-subtle {
            color: #94a3b8;
            margin-top: -0.35rem;
            margin-bottom: 1.2rem;
        }
        div[data-testid="stMetric"] {
            background: rgba(15, 23, 42, 0.42);
            border: 1px solid rgba(148, 163, 184, 0.18);
            border-radius: 8px;
            padding: 0.85rem 0.95rem;
        }
        div[data-testid="stMetricLabel"] p {
            color: #94a3b8;
            font-size: 0.78rem;
        }
        div[data-testid="stMetricValue"] {
            font-size: 1.45rem;
        }
        div[data-testid="stTabs"] button p {
            font-size: 0.92rem;
            font-weight: 600;
        }
        .settings-section-note {
            color: #94a3b8;
            margin: -0.35rem 0 1rem 0;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _render_header() -> None:
    st.markdown('<div class="settings-kicker">Operations</div>', unsafe_allow_html=True)
    st.title("Settings")
    st.markdown(
        '<div class="settings-subtle">Keep portfolio inputs, fund data, cache jobs, and database health in one place.</div>',
        unsafe_allow_html=True,
    )


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
        for col in ("navStatus", "holdingsStatus", "metadataStatus"):
            pending_sources += tracked.filter(pl.col(col) == "pending").height
            unavailable_sources += tracked.filter(pl.col(col) == "unavailable").height

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
    portfolio_tab, fund_tab, system_tab, reference_tab = st.tabs(
        ["Portfolio Data", "Fund Data", "System", "Reference"]
    )

    with portfolio_tab:
        st.markdown("### Portfolio Data")
        st.markdown(
            '<div class="settings-section-note">Upload and inspect the Kite/Zerodha tradebook used by Portfolio analytics.</div>',
            unsafe_allow_html=True,
        )
        tradebook.render()

    with fund_tab:
        st.markdown("### Fund Data")
        st.markdown(
            '<div class="settings-section-note">Sync the AMFI universe, refresh tracked funds, and rebuild derived metrics.</div>',
            unsafe_allow_html=True,
        )
        amfi.render()
        refresh.render()
        metrics_cache.render()

    with system_tab:
        st.markdown("### System")
        st.markdown(
            '<div class="settings-section-note">Inspect database footprint and table-level storage.</div>',
            unsafe_allow_html=True,
        )
        db_stats.render()

    with reference_tab:
        st.markdown("### Reference")
        st.markdown(
            '<div class="settings-section-note">Source-to-table mappings and schema notes for maintenance work.</div>',
            unsafe_allow_html=True,
        )
        data_sources.render()


_inject_styles()
_render_header()
_render_overview()
st.divider()
_render_tabs()
