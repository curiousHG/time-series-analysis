import streamlit as st
import polars as pl
import pandas as pd
import json

from streamlit_lightweight_charts import renderLightweightCharts
from ui.components.stock_picker import stock_picker
from ui.state.loaders import load_stock_open_close


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

    # Moving averages
    sdf["ma20"] = sdf["Close"].rolling(20).mean()
    sdf["ma50"] = sdf["Close"].rolling(50).mean()

    # Prepare chart data
    candles = json.loads(
        sdf[["time", "Open", "High", "Low", "Close"]]
        .rename(
            columns={"Open": "open", "High": "high", "Low": "low", "Close": "close"}
        )
        .dropna()
        .to_json(orient="records")
    )

    volume_data = json.loads(
        sdf[["time", "Volume"]]
        .rename(columns={"Volume": "value"})
        .dropna()
        .to_json(orient="records")
    )

    # Color volume bars green/red based on close vs open
    for i, row in enumerate(volume_data):
        if i < len(candles):
            row["color"] = (
                "rgba(38, 166, 154, 0.5)"
                if candles[i]["close"] >= candles[i]["open"]
                else "rgba(239, 83, 80, 0.5)"
            )

    ma20_data = json.loads(
        sdf[["time", "ma20"]]
        .rename(columns={"ma20": "value"})
        .dropna()
        .to_json(orient="records")
    )

    ma50_data = json.loads(
        sdf[["time", "ma50"]]
        .rename(columns={"ma50": "value"})
        .dropna()
        .to_json(orient="records")
    )

    chart_options = {
        "height": 500,
        "layout": {
            "background": {"color": "#0f1117"},
            "textColor": "#e2e8f0",
        },
        "grid": {
            "vertLines": {"color": "#1e293b"},
            "horzLines": {"color": "#1e293b"},
        },
        "crosshair": {"mode": 0},
        "timeScale": {"borderColor": "#334155"},
        "rightPriceScale": {"borderColor": "#334155"},
    }

    series = [
        {
            "type": "Candlestick",
            "data": candles,
            "options": {
                "upColor": "#26a69a",
                "downColor": "#ef5350",
                "borderUpColor": "#26a69a",
                "borderDownColor": "#ef5350",
                "wickUpColor": "#26a69a",
                "wickDownColor": "#ef5350",
            },
        },
        {
            "type": "Line",
            "data": ma20_data,
            "options": {
                "color": "#6366f1",
                "lineWidth": 1,
                "title": "MA20",
            },
        },
        {
            "type": "Line",
            "data": ma50_data,
            "options": {
                "color": "#f59e0b",
                "lineWidth": 1,
                "title": "MA50",
            },
        },
        {
            "type": "Histogram",
            "data": volume_data,
            "options": {
                "priceFormat": {"type": "volume"},
                "priceScaleId": "",
            },
        },
    ]

    st.subheader(symbol)
    renderLightweightCharts(
        [{"chart": chart_options, "series": series}],
        key=f"chart_{symbol}",
    )
