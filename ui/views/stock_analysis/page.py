"""Stock Analysis page — candlestick chart, fundamentals, and strategy backtest tabs."""

from __future__ import annotations

import pandas as pd
import polars as pl
import streamlit as st

from data.repositories.stock import get_stock_ohlcv_statuses, list_index_symbols
from indicators import INDICATOR_REGISTRY, compute_indicators
from stocks.constants import to_bare_symbol
from ui.components.notifications import render_toasts
from ui.persistence.selections import load_selection, save_selection
from ui.state.loaders import load_index_ohlcv, load_stock_open_close
from ui.views.stock_analysis import chart as chart_tab
from ui.views.stock_analysis import fundamentals as fundamentals_tab
from ui.views.stock_analysis import strategy_backtest as backtest_tab

_NO_INDEX = "— none —"


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

# Instrument selection: the watchlist stock picker + a separate index picker (indices override).
if st.session_state.get("stock_analysis_symbol") not in symbols:
    st.session_state.pop("stock_analysis_symbol", None)

_index_options = [_NO_INDEX, *list_index_symbols()]
if st.session_state.get("stock_analysis_index") not in _index_options:
    st.session_state.pop("stock_analysis_index", None)

col_stock, col_index = st.columns(2)
with col_stock:
    symbol = st.selectbox("Stock", symbols, key="stock_analysis_symbol")
with col_index:
    index_pick = st.selectbox("Index (overrides stock)", _index_options, key="stock_analysis_index")
index_sel = None if index_pick in (None, _NO_INDEX) else index_pick

with st.expander("Add an index", icon=":material/add:"):
    from services.benchmarks import BENCHMARK_CHOICES

    _label = st.selectbox("Curated indices", list(BENCHMARK_CHOICES), key="stock_analysis_add_index")
    if st.button("Add index", key="add_index_btn"):
        from data.repositories.stock import ensure_index_data

        _sym = BENCHMARK_CHOICES[_label]
        with st.spinner(f"Fetching {_label}…"):
            ensure_index_data(_sym, pd.to_datetime("2000-01-01"), pd.Timestamp.today().normalize())
        load_index_ohlcv.clear()
        st.session_state["stock_analysis_index"] = _sym
        st.rerun()

if index_sel:
    # Index view — chart only (screener.in fundamentals / CAPM backtest are equity-only).
    idf = load_index_ohlcv(index_sel, pd.to_datetime("2000-01-01"), pd.Timestamp.today().normalize())
    render_toasts()
    if idf.is_empty():
        st.warning(f"No price history for **{index_sel}**.")
    else:
        st.caption(f"Viewing index **{index_sel}** — clear it (— none —) to return to stocks.")
        _render_chart(_chart_pdf(idf, index_sel), index_sel)
elif symbol:
    sdf = _chart_pdf(df, symbol)
    tab_chart, tab_fundamentals, tab_backtest = st.tabs(["Chart", "Fundamentals", "Strategy Backtest"])
    with tab_chart:
        _render_chart(sdf, symbol)
    with tab_fundamentals:
        fundamentals_tab.render(symbol)
    with tab_backtest:
        backtest_tab.render(sdf, symbol)
