import streamlit as st
from ui.sidebar import sidebar
from ui.views.backtest import render as render_backtest
from ui.views.mutual_funds import render as render_mf

st.set_page_config(layout="wide")

state = sidebar()

if state["mode"] == "Stocks":
    render_backtest(state)

elif state["mode"] == "Mutual Funds":
    render_mf(state)
