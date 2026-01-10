import streamlit as st
import polars as pl


def stock_picker():

    st.sidebar.markdown("### 🔍 Select Stocks To Analyze")

    if "selected_stocks" not in st.session_state:
        st.session_state.selected_stocks = ["AAPL", "MSFT", "GOOGL"]

    options = sorted(
        set(st.session_state.selected_stocks)
    )
    st.sidebar.multiselect(
        "Selected Stocks",
        options=options,
        key="selected_stocks",
    )

    return st.session_state.selected_stocks