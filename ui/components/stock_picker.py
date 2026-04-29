import streamlit as st

from ui.persistence.selections import load_selection, save_selection
from ui.state.loaders import cached_search_stock


def option_formatter(item: tuple[str, str]) -> str:
    return f"{item[0]} - {item[1]}"


def on_selected_stocks_change():
    st.session_state.selected_stocks = list(st.session_state.selected_stocks_widget)
    save_selection("selected_stocks", st.session_state.selected_stocks)


def on_add_selected():
    to_add = st.session_state.yfinance_selected
    new_symbols = [symbol for symbol, _ in to_add]

    merged = sorted(set(st.session_state.selected_stocks + new_symbols))
    st.session_state.selected_stocks = merged
    st.session_state.selected_stocks_widget = merged
    st.session_state.yfinance_selected = []

    save_selection("selected_stocks", merged)


def stock_picker():
    st.sidebar.markdown("### Select Stocks To Analyze")

    if "selected_stocks" not in st.session_state:
        st.session_state.selected_stocks = load_selection("selected_stocks", [])

    if "selected_stocks_widget" not in st.session_state or st.session_state.pop("_load_stock_defaults", False):
        st.session_state.selected_stocks_widget = st.session_state.selected_stocks.copy()

    if "yfinance_symbols" not in st.session_state:
        st.session_state.yfinance_symbols = []

    if "yfinance_selected" not in st.session_state:
        st.session_state.yfinance_selected = []

    st.sidebar.multiselect(
        "Selected Stocks",
        options=sorted(st.session_state.selected_stocks),
        key="selected_stocks_widget",
        on_change=on_selected_stocks_change,
    )

    st.sidebar.markdown("### Add stock from yfinance")

    query = st.sidebar.text_input(
        "Search stock",
        placeholder="Type stock name ...",
        key="yfinance_query",
    )

    if query and len(query) >= 3:
        with st.spinner("Searching..."):
            yf_df = cached_search_stock(query).reset_index()

            st.session_state.yfinance_symbols = list(zip(yf_df["symbol"], yf_df["shortName"]))
    else:
        st.session_state.yfinance_symbols = []

    if st.session_state.yfinance_symbols:
        st.sidebar.multiselect(
            "Search results",
            options=st.session_state.yfinance_symbols,
            format_func=option_formatter,
            key="yfinance_selected",
        )

        st.sidebar.button(
            "Add selected",
            on_click=on_add_selected,
        )

    # ---- Defaults
    st.sidebar.divider()
    dc1, dc2 = st.sidebar.columns(2)
    if dc1.button("Save as default", key="save_default_stocks", use_container_width=True):
        save_selection("default_stocks", st.session_state.selected_stocks)
        st.sidebar.success(f"Saved {len(st.session_state.selected_stocks)} stocks as default")
    if dc2.button("Load defaults", key="load_default_stocks", use_container_width=True):
        defaults = load_selection("default_stocks", [])
        if defaults:
            st.session_state.selected_stocks = defaults
            save_selection("selected_stocks", defaults)
            st.session_state._load_stock_defaults = True
            st.rerun()
        else:
            st.sidebar.warning("No defaults saved yet")

    return st.session_state.selected_stocks
