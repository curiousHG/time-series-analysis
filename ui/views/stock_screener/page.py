"""Stock Screener — filter NSE stocks on fundamentals + categorise by CAPM alpha vs Nifty.

Seeded from the Nifty 50 (screener.in fundamentals + price-derived alpha/beta). The headline
is the alpha-categorisation scatter; an AgGrid table (same interaction model as the MF
screener) sits beneath it — click a Symbol to open the stock in Stock Analysis.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from services.stock_screener_service import apply_stock_filters
from stocks.constants import NIFTY_50
from stocks.metric_catalog import CATEGORY_COLORS, DEFAULT_VISIBLE_COLS, STOCK_METRIC_RENAME
from ui.components.aggrid_theme import streamlit_dark_aggrid_theme
from ui.components.screener_grid import clicked_cell_value, render_screener_grid
from ui.constants import STOCK_FILTER_DEFAULTS, STOCK_SCREENER_PERSIST_KEY
from ui.persistence.selections import load_selection, save_selection
from ui.state.filter_persistence import hydrate_filters, make_persist_callback
from ui.state.loaders import cached_search_stock, load_stock_open_close, load_stock_screener_df_cached
from ui.state.navigation import open_stock_in_analysis
from ui.views.stock_screener import chart as chart_view

_TEXT_COLS = frozenset({"Symbol", "Name", "Alpha Category"})

_persist_filters = make_persist_callback(STOCK_SCREENER_PERSIST_KEY, STOCK_FILTER_DEFAULTS)


def _none_if_zero(x: float) -> float | None:
    return x if x else None


def _populate(symbols: list[str]) -> None:
    from services.stock_sync_service import sync_stocks  # noqa: PLC0415 — defer heavy import off boot

    with st.spinner(f"Scraping screener.in + computing CAPM alpha for {len(symbols)} stocks… (a few minutes)"):
        sync_stocks(symbols)
    load_stock_screener_df_cached.clear()


def _add_stocks(yf_symbols: list[str]) -> None:
    """Add stocks to the universe from a yfinance search: pull full OHLCV history into the DB,
    scrape fundamentals + compute CAPM metrics, and add them to the Stock Analysis watchlist."""
    from data.repositories.stock import ensure_stock_data  # noqa: PLC0415 — defer heavy import off boot
    from services.stock_sync_service import sync_stocks  # noqa: PLC0415

    wide_start = pd.to_datetime("2000-01-01")
    today = pd.Timestamp.today()
    bare = [s.removesuffix(".NS") for s in yf_symbols]  # screener.in + stock_metrics use the bare NSE symbol
    with st.spinner(f"Pulling full history + computing metrics for {len(yf_symbols)} stock(s)…"):
        for yf_sym in yf_symbols:
            ensure_stock_data(yf_sym, wide_start, today)  # whole-range OHLCV → stock_ohlcv (DB-first, fetches the gap)
        sync_stocks(bare)  # screener.in fundamentals + CAPM alpha/beta → stock_metrics

    watchlist = sorted({*load_selection("selected_stocks", []), *yf_symbols})
    save_selection("selected_stocks", watchlist)
    st.session_state.selected_stocks = watchlist
    load_stock_screener_df_cached.clear()  # new rows appear in the table
    load_stock_open_close.clear()  # Stock Analysis picks up the freshly pulled history


st.title("Stock Screener")

with st.expander("Add stocks to the universe", expanded=False, icon=":material/add:"):
    st.caption(
        "Search Yahoo Finance for NSE stocks. Adding one pulls its full price history into the "
        "database, scrapes fundamentals, and computes CAPM alpha/beta — it then shows up in the "
        "table below and in the Stock Analysis picker."
    )
    _q = st.text_input("Search by name or symbol", placeholder="e.g. tata motors", key="stock_scr_add_query")
    _opts: list[tuple[str, str]] = []
    if _q and len(_q) >= 3:
        with st.spinner("Searching…"):
            _res = cached_search_stock(_q).reset_index()
        if not _res.empty:
            _opts = list(zip(_res["symbol"], _res["shortName"], strict=False))
    _picked = st.multiselect(
        "Results",
        options=_opts,
        format_func=lambda t: f"{t[0]} — {t[1]}",
        key="stock_scr_add_picked",
    )
    if st.button("Add selected", type="primary", disabled=not _picked):
        _add_stocks([sym for sym, _ in _picked])
        st.rerun()

_df = load_stock_screener_df_cached()

if _df.is_empty():
    st.info("No stock data cached yet. Populate the Nifty 50 sample to get started.")
    if st.button("Populate Nifty 50 (scrape + compute alpha)", type="primary"):
        _populate(list(NIFTY_50))
        st.rerun()
    st.stop()

_defaults = dict(STOCK_FILTER_DEFAULTS)
_defaults["stock_scr_visible"] = list(DEFAULT_VISIBLE_COLS)
hydrate_filters(STOCK_SCREENER_PERSIST_KEY, _defaults)

with st.sidebar:
    st.header("Filters")
    name_query = st.text_input("Search name / symbol", key="stock_scr_name", on_change=_persist_filters)
    categories = st.multiselect(
        "Alpha category", list(CATEGORY_COLORS), key="stock_scr_cats", on_change=_persist_filters
    )
    mcap_min = st.number_input(
        "Min Market Cap (Cr)", min_value=0.0, step=1000.0, key="stock_scr_mcap", on_change=_persist_filters
    )
    pe_max = st.number_input(
        "Max P/E (0 = any)", min_value=0.0, step=1.0, key="stock_scr_pe", on_change=_persist_filters
    )
    roe_min = st.number_input("Min ROE %", min_value=0.0, step=1.0, key="stock_scr_roe", on_change=_persist_filters)
    alpha_min = st.number_input("Min Alpha %", step=1.0, key="stock_scr_alpha", on_change=_persist_filters)
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

# Column-visibility multiselect + AgGrid table; click a Symbol to open Stock Analysis.
visible_metrics = st.multiselect(
    "Visible metrics",
    options=[c for c in STOCK_METRIC_RENAME.values() if c != "Symbol"],
    key="stock_scr_visible",
    on_change=_persist_filters,
    help="Columns to display; Symbol is always shown.",
)

display = _filtered.rename({k: v for k, v in STOCK_METRIC_RENAME.items() if k in _filtered.columns})
if "Alpha %" in display.columns:
    display = display.sort("Alpha %", descending=True, nulls_last=True)
_display_cols = ["Symbol", *[c for c in STOCK_METRIC_RENAME.values() if c in visible_metrics and c in display.columns]]
display_pdf = display.select(_display_cols).to_pandas()

st.caption("Click a **Symbol** to open the stock in Stock Analysis →")
_grid_response = render_screener_grid(
    display_pdf,
    numeric_cols={c for c in display_pdf.columns if c not in _TEXT_COLS},
    text_cols=_TEXT_COLS,
    theme=streamlit_dark_aggrid_theme(),
    pinned_col="Symbol",
    link_col="Symbol",
    height=520,
    pinned_min_width=140,
)

_clicked = clicked_cell_value(_grid_response, "Symbol")
if _clicked:
    open_stock_in_analysis(_clicked)
