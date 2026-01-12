import streamlit as st
import polars as pl
import pandas as pd
import vectorbt as vbt

# from ui.charts.price_chart import render_price_chart
from ui.charts.indicator_chart import render_indicator
from ui.components.stock_picker import stock_picker
from ui.tables.stats_table import render_stats
from ui.utils import make_arrow_safe
import plotly.graph_objects as go

from strategies.rsi import RSIStrategy
from ui.data.loaders import load_stock_open_close
from data.fetchers.stock import fetch_symbol_data, get_symbol_info, query_stocks
from data.store.stock import (
    ensure_stock_data,
    load_stock_registry,
    save_to_stock_registry,
)

# IMPORTANT: force plotly Figure, not widgets
vbt.settings["plotting"]["use_widgets"] = False


stock_picker()
with st.sidebar:
    st.markdown("### 📅 Date Range")

    start_date, end_date = st.date_input(
        "Select date range",
        value=(pd.to_datetime("2022-01-01"), pd.to_datetime("2023-01-03")),
        min_value=pd.to_datetime("2000-01-01"),
        max_value=pd.Timestamp.today(),
        key="date_range",
    )
# st.write(stocks)
df = load_stock_open_close(st.session_state.selected_stocks, start_date, end_date)
# st.dataframe(df)
# st.write(ensure_stock_data("RELIANCE.NS", start="2022-01-01", end="2023-01-03"))


symbol = st.selectbox(
    "Select stock", df.select("Symbol").unique().to_series().to_list()
)
if symbol:
    sdf = df.filter(pl.col("Symbol") == symbol).to_pandas()

    fig = go.Figure(
        data=[
            go.Candlestick(
                x=sdf["Date"],
                open=sdf["Open"],
                high=sdf["High"],
                low=sdf["Low"],
                close=sdf["Close"],
            )
        ]
    )

    fig.update_layout(title=f"{symbol} Candlestick")
    st.plotly_chart(fig, use_container_width=True)


# @st.cache_data
# def load_data(symbol: str):
#     return pl.read_parquet(f"data/parquet/{symbol}.parquet")


# st.title("📈 Backtest")
# symbol = "RELIANCE.NS"
# params = {}

# # example
# window = params.get("window", 14)

# # ---------- Data ----------
# df = load_data(symbol)
# price = pd.Series(df["Close"].to_numpy(), index=df["Date"])

# # ---------- Strategy ----------
# strategy = RSIStrategy(window=window)

# indicators = strategy.indicators(price)
# entries, exits = strategy.signals(price, indicators)

# pf = vbt.Portfolio.from_signals(
#     price,
#     entries,
#     exits,
#     freq="1D"
# )

# # ---------- Layout ----------
# left, right = st.columns([3, 2])

# with left:
#     st.subheader("📉 Price Chart with Indicators")
#     st.plotly_chart(pf.plot(), width="stretch")

#     for name, series in indicators.items():
#         fig = render_indicator(name, series)
#         st.plotly_chart(fig, width="stretch")

# with right:
#     render_stats(pf)

#     st.subheader("Trades")
#     st.dataframe(
#         make_arrow_safe(pf.trades.records_readable)
#     )
