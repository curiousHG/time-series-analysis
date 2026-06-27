"""Stock Analysis page — Streamlit entry. Two tabs: candlestick chart + strategy backtest."""

from __future__ import annotations

import pandas as pd
import polars as pl
import streamlit as st

from indicators import INDICATOR_REGISTRY, compute_indicators
from ui.components.stock_picker import stock_picker
from ui.state.loaders import load_stock_open_close
from ui.views.stock_analysis import chart as chart_tab
from ui.views.stock_analysis import strategy_backtest as backtest_tab

stock_picker()

# Load the full available history; the chart opens focused on the last year and the
# Daily/Weekly/Monthly buttons control candle aggregation (see chart tab).
df = load_stock_open_close(
    st.session_state.selected_stocks,
    pd.to_datetime("2000-01-01"),
    pd.Timestamp.today(),
)
symbols = df.select("Symbol").unique().to_series().to_list()
# Drop a stale pre-selection (e.g. from a screener click whose OHLCV didn't load) so the
# keyed selectbox never gets a value outside its options.
if st.session_state.get("stock_analysis_symbol") not in symbols:
    st.session_state.pop("stock_analysis_symbol", None)
symbol = st.selectbox("Select stock", symbols, key="stock_analysis_symbol")

if symbol:
    sdf = (
        df.filter(pl.col("Symbol") == symbol)
        .sort("Date")
        .with_columns(pl.col("Date").cast(pl.Utf8).alias("time"))
        .to_pandas()
    )

    tab_chart, tab_backtest = st.tabs(["Chart", "Strategy Backtest"])

    with tab_chart:
        interval = st.segmented_control(
            "Candle interval",
            options=["Daily", "Weekly", "Monthly"],
            default="Daily",
            key="candle_interval",
        )
        chart_df = chart_tab.resample_ohlc(sdf, interval or "Daily")

        with st.sidebar:
            st.markdown("### Indicators")
            overlay_names = [n for n, e in INDICATOR_REGISTRY.items() if e["overlay"]]
            panel_names = [n for n, e in INDICATOR_REGISTRY.items() if not e["overlay"]]
            selected_overlays = st.multiselect(
                "Overlays (on price chart)",
                options=overlay_names,
                default=["SMA 20", "SMA 50"],
                key="selected_overlays",
            )
            selected_panels = st.multiselect(
                "Panels (below chart)",
                options=panel_names,
                default=[],
                key="selected_panels",
            )

        selected_indicators = selected_overlays + selected_panels
        overlays, panels = compute_indicators(chart_df, selected_indicators)
        chart_tab.render(chart_df, overlays, panels, selected_panels, symbol)

    with tab_backtest:
        backtest_tab.render(sdf, symbol)
