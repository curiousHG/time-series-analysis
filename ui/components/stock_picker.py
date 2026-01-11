import streamlit as st
import polars as pl
from ui.persistence.cookies import get_cookie


def stock_picker():

    st.sidebar.markdown("### 🔍 Select Stocks To Analyze")

    if "selected_stocks" not in st.session_state:
        st.session_state.selected_stocks = get_cookie("selected_stock", []) or []
    
    if "yfinance_symbols" not in st.session_state:
        st.session_state.yfinance_symbols = []

    options = sorted(
        set(st.session_state.selected_stocks)
    )
    st.sidebar.multiselect(
        "Selected Stocks",
        options=options,
        key="selected_stocks",
    )
    
    st.sidebar.markdown("### Add stock from yfinance")
    query = st.sidebar.text_input("Search stock", placeholder="Type stock name ...", key = "yfinance_query")
    
    if query and len(query) >= 3:
        with st.spinner("Searching..."):
            pass
            # yf_df = cached_search_stock(query)
            # st.session_state.yfinance_symbols =  yf_df

    return st.session_state.selected_stocks