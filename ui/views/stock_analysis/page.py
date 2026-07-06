"""Stock Analysis page — candlestick chart, fundamentals, and strategy backtest tabs."""

from __future__ import annotations

import pandas as pd
import polars as pl
import streamlit as st

from data.repositories.stock import get_stock_ohlcv_statuses, list_bhavcopy_index_names
from indicators import INDICATOR_REGISTRY, compute_indicators
from services.benchmarks import index_display_name
from services.insights_service import INTERNATIONAL_INDEX_SYMBOLS
from stocks.constants import to_bare_symbol
from ui.components import index_detail
from ui.components.notifications import render_toasts
from ui.persistence.selections import load_selection, save_selection
from ui.state.loaders import load_index_chart_ohlcv, load_stock_open_close
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


def _refresh_stock_on_open(ticker: str, frame: pl.DataFrame) -> None:
    """On the first open of a stock this session, pull its OHLCV forward to today (cheap no-op when
    already fresh); reload only if new bars actually arrived. Fundamentals are fetched by the tab."""
    import contextlib  # noqa: PLC0415

    from data.repositories.stock import refresh_stock_to_today  # noqa: PLC0415 — defer off boot

    bare = to_bare_symbol(ticker)
    opened = st.session_state.setdefault("_sa_opened", set())
    if bare in opened:
        return
    opened.add(bare)
    shown = frame.filter(pl.col("Symbol") == bare)
    shown_max = shown.select(pl.col("Date").max()).item() if shown.height else None
    new_max = None
    with st.spinner(f"Refreshing {ticker}…"), contextlib.suppress(Exception):
        _, new_max = refresh_stock_to_today(bare)
    if new_max is not None and (shown_max is None or new_max > shown_max):
        load_stock_open_close.clear()
        st.rerun()


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


def _stock_view(ticker: str, frame: pl.DataFrame) -> None:
    """Equity view: chart · fundamentals · backtest. Refreshes OHLCV on first open."""
    _refresh_stock_on_open(ticker, frame)
    sdf = _chart_pdf(frame, ticker)
    tab_chart, tab_fundamentals, tab_backtest = st.tabs(["Chart", "Fundamentals", "Strategy Backtest"])
    with tab_chart:
        _render_chart(sdf, ticker)
    with tab_fundamentals:
        fundamentals_tab.render(ticker)
    with tab_backtest:
        backtest_tab.render(sdf, ticker)


def _index_view(ticker: str) -> None:
    """Index view: valuation (P/E·P/B·Div-Yield·turnover) + trailing return + chart + constituents.
    Screener.in fundamentals / CAPM backtest are equity-only, so those tabs are replaced here."""
    label = index_display_name(ticker) if ticker.startswith("^") else ticker
    idf = load_index_chart_ohlcv(ticker, pd.to_datetime("2000-01-01"), pd.Timestamp.today().normalize())
    render_toasts()
    st.subheader(label)
    index_detail.render_valuation(ticker)
    if idf.is_empty():
        st.warning(f"No price history for **{label}** yet.")
        return
    index_detail.render_trailing(idf)
    tab_chart, tab_constituents = st.tabs(["Chart", "Constituents"])
    with tab_chart:
        _render_chart(_chart_pdf(idf, ticker), label)
    with tab_constituents:
        if ticker.startswith("^"):
            st.caption("Constituent breakdown isn't available for foreign indices.")
        else:
            index_detail.render_constituents(ticker)


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

# Ticker picker — split into Stock vs Index sections (they were confusingly mixed in one list).
# Stocks come from your watchlist (Stock Screener "Add ticker"); indices are the clean NSE-name set
# (160+ incl. factor/strategy) + international, keyed off the index_ohlcv bhavcopy data.
_kind = st.radio("Type", ["Stock", "Index"], horizontal=True, key="sa_ticker_kind", label_visibility="collapsed")

if _kind == "Stock":
    if not symbols:
        st.info("No stocks in your watchlist yet — add some from the **Stock Screener**.")
        st.stop()
    if st.session_state.get("sa_stock_sel") not in symbols:
        st.session_state.pop("sa_stock_sel", None)
    ticker = st.selectbox(f"Stock · {len(symbols)} in watchlist", symbols, key="sa_stock_sel")
    _stock_view(ticker, df)
else:
    _nse = list_bhavcopy_index_names()
    _intl = [s for s, _, _ in INTERNATIONAL_INDEX_SYMBOLS]
    _idx_opts = _nse + _intl
    if not _idx_opts:
        st.info("No index data yet — refresh from **Settings > Stock Data**.")
        st.stop()
    if st.session_state.get("sa_index_sel") not in _idx_opts:
        st.session_state.pop("sa_index_sel", None)
    ticker = st.selectbox(
        f"Index · {len(_nse)} NSE + {len(_intl)} international",
        _idx_opts,
        format_func=lambda s: index_display_name(s) if s.startswith("^") else s,
        key="sa_index_sel",
    )
    _index_view(ticker)
