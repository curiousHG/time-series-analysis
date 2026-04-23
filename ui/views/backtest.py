import streamlit as st
import polars as pl
import pandas as pd

from ui.components.stock_picker import stock_picker
from ui.state.loaders import load_stock_open_close
from core.indicators import INDICATOR_REGISTRY, compute_indicators

from ui.views.stock_tabs import chart as chart_tab
from ui.views.stock_tabs import strategy_backtest as backtest_tab


stock_picker()

with st.sidebar:
    st.markdown("### Date Range")
    start_date, end_date = st.date_input(
        "Select date range",
        value=(pd.to_datetime("2022-01-01"), pd.to_datetime("2023-01-03")),
        min_value=pd.to_datetime("2000-01-01"),
        max_value=pd.Timestamp.today(),
        key="date_range",
    )

df = load_stock_open_close(st.session_state.selected_stocks, start_date, end_date)

symbols = df.select("Symbol").unique().to_series().to_list()
symbol = st.selectbox("Select stock", symbols)

if symbol:
    sdf = (
        df.filter(pl.col("Symbol") == symbol)
        .sort("Date")
        .with_columns(pl.col("Date").cast(pl.Utf8).alias("time"))
        .to_pandas()
    )

    tab_chart, tab_backtest = st.tabs(["Chart", "Strategy Backtest"])

    with tab_chart:
        # Indicator sidebar controls
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
        overlays, panels = compute_indicators(sdf, selected_indicators)
        chart_tab.render(sdf, overlays, panels, selected_panels, symbol)

    with tab_backtest:
        backtest_tab.render(sdf, symbol)
