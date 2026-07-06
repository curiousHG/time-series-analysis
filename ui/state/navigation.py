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
    """Pre-select `scheme_name` on the MF Analysis page and switch to it."""
    st.session_state["mf_analysis_fund"] = scheme_name
    st.switch_page(MF_ANALYSIS_PAGE)


def open_stock_in_analysis(symbol: str) -> None:
    """Add `symbol` to the Stock Analysis watchlist (bare canonical form), select it, switch."""
    from stocks.constants import to_bare_symbol  # noqa: PLC0415 — avoid a domain import at module load

    bare = to_bare_symbol(symbol)
    existing = {to_bare_symbol(s) for s in st.session_state.get("selected_stocks", [])}  # heal legacy .NS
    selected = sorted(existing | {bare})
    st.session_state.selected_stocks = selected
    save_selection("selected_stocks", selected)
    st.session_state.stock_analysis_symbol = bare
    st.session_state.sa_ticker_kind = "Stock"  # land on the Stock section of the picker
    st.switch_page(STOCK_ANALYSIS_PAGE)
