"""Cross-page navigation helpers — each owns the target page's session-state contract.

Screeners (and any future page) call these instead of writing another page's private
widget keys inline, so the contract lives in exactly one place.
"""

from __future__ import annotations

import streamlit as st

from ui.persistence.selections import save_selection

# Page paths for st.switch_page / st.page_link — keep in sync with ui/app.py.
OVERVIEW_PAGE = "ui/views/overview/page.py"
PORTFOLIO_PAGE = "ui/views/portfolio/page.py"
MF_ANALYSIS_PAGE = "ui/views/mutual_fund/page.py"
MF_SCREENER_PAGE = "ui/views/mf_screener/page.py"
STOCK_ANALYSIS_PAGE = "ui/views/stock_analysis/page.py"
STOCK_SCREENER_PAGE = "ui/views/stock_screener/page.py"
SETTINGS_PAGE = "ui/views/settings/page.py"


def open_fund_in_analysis(scheme_name: str) -> None:
    """Pre-select `scheme_name` on the MF Analysis page and switch to it.

    Clears the Analysis page's filters so the opened fund stays in the selectbox options
    (a persisted filter could otherwise exclude it).
    """
    st.session_state["mf_analysis_fund"] = scheme_name
    st.session_state["mf_analysis_search"] = ""
    for k in ("mf_analysis_amc", "mf_analysis_cat", "mf_analysis_sub_cat", "mf_analysis_plan", "mf_analysis_option"):
        st.session_state[k] = []
    st.session_state["mf_analysis_min_age"] = 0.0
    st.session_state["mf_analysis_only_meta"] = False
    st.session_state["mf_analysis_only_holdings"] = False
    st.switch_page(MF_ANALYSIS_PAGE)


def open_stock_in_analysis(symbol: str) -> None:
    """Add `symbol` to the Stock Analysis watchlist (yfinance `.NS` form), select it, switch."""
    yf_sym = symbol if symbol.endswith(".NS") or symbol.startswith("^") else f"{symbol}.NS"
    selected = sorted({*st.session_state.get("selected_stocks", []), yf_sym})
    st.session_state.selected_stocks = selected
    st.session_state.selected_stocks_widget = selected
    save_selection("selected_stocks", selected)
    st.session_state.stock_analysis_symbol = yf_sym
    st.switch_page(STOCK_ANALYSIS_PAGE)
