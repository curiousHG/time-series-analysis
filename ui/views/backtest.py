import streamlit as st
import polars as pl
import pandas as pd
import vectorbt as vbt

# from ui.charts.price_chart import render_price_chart
from ui.charts.indicator_chart import render_indicator
from ui.tables.stats_table import render_stats
from ui.utils import make_arrow_safe

from strategies.rsi import RSIStrategy

# IMPORTANT: force plotly Figure, not widgets
vbt.settings['plotting']['use_widgets'] = False


@st.cache_data
def load_data(symbol: str):
    return pl.read_parquet(f"data/parquet/{symbol}.parquet")




st.title("📈 Backtest")

# ---------- Sidebar ----------
# symbol = state["symbol"]
# strategy_name = state["strategy"]
# params = state["params"]
symbol = "RELIANCE.NS"
params = {}

# example
window = params.get("window", 14)

# ---------- Data ----------
df = load_data(symbol)
price = pd.Series(df["Close"].to_numpy(), index=df["Date"])

# ---------- Strategy ----------
strategy = RSIStrategy(window=window)

indicators = strategy.indicators(price)
entries, exits = strategy.signals(price, indicators)

pf = vbt.Portfolio.from_signals(
    price,
    entries,
    exits,
    freq="1D"
)

# ---------- Layout ----------
left, right = st.columns([3, 2])

with left:
    st.subheader("📉 Price Chart with Indicators")
    st.plotly_chart(pf.plot(), width="stretch")

    for name, series in indicators.items():
        fig = render_indicator(name, series)
        st.plotly_chart(fig, width="stretch")

with right:
    render_stats(pf)

    st.subheader("Trades")
    st.dataframe(
        make_arrow_safe(pf.trades.records_readable)
    )
