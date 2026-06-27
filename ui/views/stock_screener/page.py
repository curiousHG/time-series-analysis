"""Stock Screener — filter NSE stocks on fundamentals + categorise by CAPM alpha vs Nifty.

Seeded from the Nifty 50 (screener.in fundamentals + price-derived alpha/beta). The headline
is the alpha-categorisation scatter; a filterable table sits beneath it.
"""

from __future__ import annotations

import streamlit as st

from services.stock_screener_service import apply_stock_filters
from stocks.constants import NIFTY_50
from stocks.metric_catalog import CATEGORY_COLORS, DEFAULT_VISIBLE_COLS, STOCK_METRIC_RENAME
from ui.persistence.selections import save_selection
from ui.state.loaders import load_stock_screener_df_cached
from ui.views.stock_screener import chart as chart_view


def _none_if_zero(x: float) -> float | None:
    return x if x else None


def _open_in_analysis(symbol: str) -> None:
    """Add the stock to the analysis watchlist (yfinance `.NS` form) and switch pages."""
    yf_sym = symbol if symbol.endswith(".NS") or symbol.startswith("^") else f"{symbol}.NS"
    selected = sorted({*st.session_state.get("selected_stocks", []), yf_sym})
    st.session_state.selected_stocks = selected
    st.session_state.selected_stocks_widget = selected
    save_selection("selected_stocks", selected)
    st.session_state.stock_analysis_symbol = yf_sym
    st.switch_page("ui/views/stock_analysis/page.py")


def _populate(symbols: list[str]) -> None:
    from services.stock_sync_service import sync_stocks  # noqa: PLC0415 — defer heavy import off boot

    with st.spinner(f"Scraping screener.in + computing CAPM alpha for {len(symbols)} stocks… (a few minutes)"):
        sync_stocks(symbols)
    load_stock_screener_df_cached.clear()


st.title("Stock Screener")

_df = load_stock_screener_df_cached()

if _df.is_empty():
    st.info("No stock data cached yet. Populate the Nifty 50 sample to get started.")
    if st.button("Populate Nifty 50 (scrape + compute alpha)", type="primary"):
        _populate(list(NIFTY_50))
        st.rerun()
    st.stop()

with st.sidebar:
    st.header("Filters")
    name_query = st.text_input("Search name / symbol", key="stock_scr_name")
    categories = st.multiselect("Alpha category", list(CATEGORY_COLORS), key="stock_scr_cats")
    mcap_min = st.number_input("Min Market Cap (Cr)", min_value=0.0, step=1000.0, key="stock_scr_mcap")
    pe_max = st.number_input("Max P/E (0 = any)", min_value=0.0, step=1.0, key="stock_scr_pe")
    roe_min = st.number_input("Min ROE %", min_value=0.0, step=1.0, key="stock_scr_roe")
    alpha_min = st.number_input("Min Alpha %", value=0.0, step=1.0, key="stock_scr_alpha")
    if st.button("Re-sync Nifty 50"):
        _populate(list(NIFTY_50))
        st.rerun()

_filtered = apply_stock_filters(
    _df,
    name_query=name_query,
    market_cap_min=_none_if_zero(mcap_min),
    pe_max=_none_if_zero(pe_max),
    roe_min=_none_if_zero(roe_min),
    alpha_min=alpha_min if alpha_min else None,
    categories=categories or None,
)

st.caption(f"**{_filtered.height}** of {_df.height} stocks match")

# Category summary — counts per alpha quadrant.
counts = dict(_filtered.group_by("alpha_category").len().iter_rows())
cols = st.columns(len(CATEGORY_COLORS))
for col, cat in zip(cols, CATEGORY_COLORS, strict=False):
    col.metric(cat, counts.get(cat, 0))

chart_view.render_alpha_chart(_filtered)

# Table — renamed display columns; click a row to open that stock in Stock Analysis.
display = _filtered.rename({k: v for k, v in STOCK_METRIC_RENAME.items() if k in _filtered.columns})
if "Alpha %" in display.columns:
    display = display.sort("Alpha %", descending=True, nulls_last=True)
table_cols = ["Symbol", *[c for c in DEFAULT_VISIBLE_COLS if c in display.columns and c != "Symbol"]]
ordered_symbols = display["Symbol"].to_list() if "Symbol" in display.columns else []

st.caption("Click a row to open the stock in **Stock Analysis** →")
event = st.dataframe(
    display.select([c for c in table_cols if c in display.columns]),
    use_container_width=True,
    hide_index=True,
    on_select="rerun",
    selection_mode="single-row",
    key="stock_scr_table",
)
_rows = event.selection.rows if event and event.selection else []
if _rows and ordered_symbols:
    _open_in_analysis(ordered_symbols[_rows[0]])
