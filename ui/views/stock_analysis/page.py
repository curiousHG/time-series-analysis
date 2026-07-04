"""Stock Analysis page — candlestick chart, fundamentals, and strategy backtest tabs."""

from __future__ import annotations

import pandas as pd
import polars as pl
import streamlit as st

from data.repositories.stock import get_stock_ohlcv_statuses, list_index_symbols
from indicators import INDICATOR_REGISTRY, compute_indicators
from services.benchmarks import index_display_name
from stocks.constants import is_index_symbol, to_bare_symbol
from ui.components.notifications import render_toasts
from ui.persistence.selections import load_selection, save_selection
from ui.state.loaders import load_index_ohlcv, load_stock_open_close
from ui.views.stock_analysis import chart as chart_tab
from ui.views.stock_analysis import fundamentals as fundamentals_tab
from ui.views.stock_analysis import strategy_backtest as backtest_tab


def _chart_pdf(frame: pl.DataFrame, sym: str) -> pd.DataFrame:
    """Slice one symbol out of an OHLCV frame into the chart's pandas shape."""
    return (
        frame.filter(pl.col("Symbol") == sym)
        .sort("Date")
        .with_columns(pl.col("Date").cast(pl.Utf8).alias("time"))
        .to_pandas()
    )


def _render_chart(sdf: pd.DataFrame, label: str) -> None:
    """Candle-interval control + indicator sidebar + chart. Shared by stocks and indices."""
    interval = st.segmented_control(
        "Candle interval", options=["Daily", "Weekly", "Monthly"], default="Daily", key="candle_interval"
    )
    chart_df = chart_tab.resample_ohlc(sdf, interval or "Daily")
    with st.sidebar:
        st.markdown("### Indicators")
        overlay_names = [n for n, e in INDICATOR_REGISTRY.items() if e["overlay"]]
        panel_names = [n for n, e in INDICATOR_REGISTRY.items() if not e["overlay"]]
        selected_overlays = st.multiselect(
            "Overlays (on price chart)", options=overlay_names, default=["SMA 20", "SMA 50"], key="selected_overlays"
        )
        selected_panels = st.multiselect("Panels (below chart)", options=panel_names, default=[], key="selected_panels")
    overlays, panels = compute_indicators(chart_df, selected_overlays + selected_panels)
    chart_tab.render(chart_df, overlays, panels, selected_panels, label)

# The watchlist is curated from the Stock Screener (search + add, or click a Symbol to open).
# Seed it from disk, canonicalising to bare symbols (legacy entries were stored `.NS`).
if "selected_stocks" not in st.session_state:
    st.session_state.selected_stocks = sorted({to_bare_symbol(s) for s in load_selection("selected_stocks", [])})

# Load the full available history; the chart opens focused on the last year and the
# Daily/Weekly/Monthly buttons control candle aggregation (see chart tab).
# `end` is normalized to midnight so this @st.cache_data loader gets a stable key within the
# day — otherwise Timestamp.today()'s sub-second component busts the cache on every rerun and
# the full per-symbol reload (spinner) runs again on each widget interaction.
df = load_stock_open_close(
    st.session_state.selected_stocks,
    pd.to_datetime("2000-01-01"),
    pd.Timestamp.today().normalize(),
)
render_toasts()  # surface any fetch errors pushed during the load

symbols = df.select("Symbol").unique().to_series().to_list()

# Auto-remove watchlist symbols confirmed to have no price data (2 empty fetches → unavailable).
_missing = [s for s in st.session_state.selected_stocks if s not in symbols]
if _missing:
    _statuses = get_stock_ohlcv_statuses(_missing)
    _dead = [s for s in _missing if _statuses.get(to_bare_symbol(s)) == "unavailable"]
    if _dead:
        st.session_state.selected_stocks = [s for s in st.session_state.selected_stocks if s not in _dead]
        save_selection("selected_stocks", st.session_state.selected_stocks)
        for s in _dead:
            st.toast(f"Removed {s} — no price data (retry in Settings > Stock Data).", icon="🗑️")

# Combined ticker picker: watchlist stocks + indices we hold data for, in one selector.
# (Tickers are added from the Stock Screener's "Add ticker" control.)
_ticker_opts = symbols + list_index_symbols()
if st.session_state.get("stock_analysis_symbol") not in _ticker_opts:
    st.session_state.pop("stock_analysis_symbol", None)

ticker = st.selectbox(
    "Ticker (stock or index)",
    _ticker_opts,
    format_func=lambda t: f"{index_display_name(t)}  ·  index" if is_index_symbol(t) else t,
    key="stock_analysis_symbol",
)

if ticker and is_index_symbol(ticker):
    # Index view — chart only (screener.in fundamentals / CAPM backtest are equity-only).
    idf = load_index_ohlcv(ticker, pd.to_datetime("2000-01-01"), pd.Timestamp.today().normalize())
    render_toasts()
    if idf.is_empty():
        st.warning(f"No price history for **{index_display_name(ticker)}**.")
    else:
        st.caption(f"Viewing index **{index_display_name(ticker)}** ({ticker}) — chart only.")
        _render_chart(_chart_pdf(idf, ticker), index_display_name(ticker))
elif ticker:
    sdf = _chart_pdf(df, ticker)
    tab_chart, tab_fundamentals, tab_backtest = st.tabs(["Chart", "Fundamentals", "Strategy Backtest"])
    with tab_chart:
        _render_chart(sdf, ticker)
    with tab_fundamentals:
        fundamentals_tab.render(ticker)
    with tab_backtest:
        backtest_tab.render(sdf, ticker)
