import streamlit as st
from ui.data.loaders import cached_search_stock
from ui.persistence.cookies import get_cookie, set_cookie


def option_formatter(item: tuple[str, str]) -> str:
    return f"{item[0]} - {item[1]}"


def on_selected_stocks_change():
    # Widget → app state sync
    st.session_state.selected_stocks = list(st.session_state.selected_stocks_widget)
    set_cookie("selected_stocks", st.session_state.selected_stocks)


def on_add_selected():
    to_add = st.session_state.yfinance_selected
    new_symbols = [symbol for symbol, _ in to_add]

    merged = sorted(set(st.session_state.selected_stocks + new_symbols))
    st.session_state.selected_stocks = merged
    st.session_state.selected_stocks_widget = merged
    st.session_state.yfinance_selected = []

    set_cookie("selected_stocks", merged)


def stock_picker(load_registry, save_to_registry):

    st.sidebar.markdown("### 🔍 Select Stocks To Analyze")

    if "selected_stocks" not in st.session_state:
        st.session_state.selected_stocks = get_cookie("selected_stocks", []) or []

    if "selected_stocks_widget" not in st.session_state:
        st.session_state.selected_stocks_widget = (
            st.session_state.selected_stocks.copy()
        )

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

            st.session_state.yfinance_symbols = list(
                zip(yf_df["symbol"], yf_df["shortName"])
            )
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

    return st.session_state.selected_stocks
