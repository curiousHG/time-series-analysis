"""Stock Screener — filter NSE stocks on fundamentals + categorise by CAPM alpha vs Nifty.

Seeded from the Nifty 50 (screener.in fundamentals + price-derived alpha/beta). The headline
is the alpha-categorisation scatter; an AgGrid table (same interaction model as the MF
screener) sits beneath it — click a Symbol to open the stock in Stock Analysis.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from services.benchmarks import BENCHMARK_CHOICES
from services.stock_screener_service import apply_stock_filters
from stocks.constants import NIFTY_50, to_bare_symbol
from stocks.metric_catalog import CATEGORY_COLORS, DEFAULT_VISIBLE_COLS, STOCK_METRIC_RENAME
from ui.components.aggrid_theme import streamlit_dark_aggrid_theme
from ui.components.background_refresh import BackgroundRefresh
from ui.components.notifications import render_toasts
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


def _populate_task(symbols: list[str]) -> int:
    from services.stock_sync_service import sync_stocks  # noqa: PLC0415 — defer heavy import off boot

    return sync_stocks(symbols)


_POPULATE = BackgroundRefresh(
    "stock_screener_populate",
    "Stock populate",
    cache_clearers=(load_stock_screener_df_cached.clear,),
    summarize=lambda r: f"{r:,} stocks synced" if isinstance(r, int) else "synced",
)


_NSE_EXCHANGES = {"NSE", "NSI", "BSE", "BO"}


def _add_stocks(picks: list[tuple[str, str, str]]) -> None:
    """Add stocks from a yfinance search (each pick = (symbol, name, exchange)). NSE stocks get
    the full flow (OHLCV + screener.in fundamentals + CAPM). Global stocks (US, etc.) get OHLCV
    only — screener.in / CAPM-vs-Nifty are India-specific — but are chartable in Stock Analysis."""
    from data.repositories.stock import ensure_stock_data, register_stock  # noqa: PLC0415 — defer off boot
    from services.stock_sync_service import sync_stocks  # noqa: PLC0415

    wide_start = pd.to_datetime("2000-01-01")
    today = pd.Timestamp.today().normalize()
    nse_bare: list[str] = []
    global_syms: list[str] = []
    for sym, name, exch in picks:
        if sym.endswith(".NS") or (exch or "").upper() in _NSE_EXCHANGES:
            nse_bare.append(to_bare_symbol(sym))
        else:
            register_stock(sym, name=name, exchange=exch or "GLOBAL", quote_type="equity")  # so it fetches as-is
            global_syms.append(sym)

    with st.spinner(f"Pulling full history for {len(nse_bare) + len(global_syms)} stock(s)…"):
        for sym in nse_bare + global_syms:
            ensure_stock_data(sym, wide_start, today)  # whole-range OHLCV → stock_ohlcv (DB-first)
        if nse_bare:
            sync_stocks(nse_bare)  # screener.in fundamentals + CAPM alpha/beta → stock_metrics (NSE only)

    existing = {to_bare_symbol(s) for s in load_selection("selected_stocks", [])}
    watchlist = sorted(existing | set(nse_bare) | set(global_syms))
    save_selection("selected_stocks", watchlist)
    st.session_state.selected_stocks = watchlist
    load_stock_screener_df_cached.clear()  # new NSE rows appear in the table
    load_stock_open_close.clear()  # Stock Analysis picks up the freshly pulled history
    st.toast(f"Added {len(picks)} stock(s).", icon="✅")


def _add_index(symbol: str, label: str) -> None:
    """Fetch a market index's full history into index_ohlcv so it's chartable in Stock Analysis."""
    from data.repositories.stock import ensure_index_data  # noqa: PLC0415 — defer heavy import off boot
    from ui.state.loaders import load_index_ohlcv  # noqa: PLC0415

    with st.spinner(f"Fetching {label}…"):
        ensure_index_data(symbol, pd.to_datetime("2000-01-01"), pd.Timestamp.today().normalize())
    load_index_ohlcv.clear()
    st.toast(f"Added index {label}.", icon="✅")


with st.expander("Add ticker (stock or index)", expanded=False, icon=":material/add:"):
    _kind = st.radio("Type", ["Stock", "Index"], horizontal=True, key="add_ticker_kind")
    if _kind == "Stock":
        st.caption(
            "Search Yahoo Finance (Indian **or** global stocks). Selections persist across "
            "searches — search, pick, search again, then add all at once. NSE stocks get "
            "fundamentals + CAPM; global stocks are OHLCV-only (chartable in Stock Analysis)."
        )
        _q = st.text_input("Search by name or symbol", placeholder="e.g. reliance, apple, tesla", key="stock_scr_add_query")
        _opts: list[tuple[str, str, str]] = []
        if _q and len(_q) >= 2:
            with st.spinner("Searching…"):
                _res = cached_search_stock(_q).reset_index()
            if not _res.empty:
                _ex = _res["exchange"] if "exchange" in _res.columns else pd.Series([""] * len(_res))
                _opts = list(zip(_res["symbol"], _res["shortName"], _ex, strict=False))
        # Migrate any legacy 2-tuple picks in session to (symbol, name, exchange) 3-tuples.
        _raw = st.session_state.get("stock_scr_add_picked", [])
        _norm = [(p[0], p[1], p[2] if len(p) > 2 else "") for p in _raw if len(p) >= 2]
        if _norm != _raw:
            st.session_state["stock_scr_add_picked"] = _norm
        # Keep already-selected options so picks survive query changes (accumulate across searches).
        _merged = _opts + [o for o in _norm if o not in _opts]
        _picked = st.multiselect(
            "Selected to add",
            options=_merged,
            format_func=lambda t: f"{t[0]} — {t[1]}" + (f"  ·  {t[2]}" if len(t) > 2 and t[2] else ""),
            key="stock_scr_add_picked",
        )
        if st.button(f"Add {len(_picked)} stock(s)", type="primary", disabled=not _picked):
            _add_stocks(_picked)
            st.rerun()
    else:
        st.caption("Pull a market index (Indian or US) into the database so it's chartable in Stock Analysis.")
        _ilabel = st.selectbox("Index", list(BENCHMARK_CHOICES), key="stock_scr_add_index")
        if st.button("Add index", type="primary"):
            _add_index(BENCHMARK_CHOICES[_ilabel], _ilabel)
            st.rerun()

render_toasts()  # surface any fetch errors from a populate/add-stocks run

_POPULATE.consume()  # swap-to-fresh + toast if a populate just finished (before we read the frame)
_df = load_stock_screener_df_cached()

if _df.is_empty():
    st.info("No stock data cached yet. Populate the Nifty 50 sample to get started.")
    _POPULATE.start_button(
        "Populate Nifty 50 (scrape + compute alpha)", lambda: _populate_task(list(NIFTY_50)), type="primary"
    )
    if _POPULATE.is_running():
        _POPULATE.poll()
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
    _POPULATE.start_button(
        "Re-sync Nifty 50", lambda: _populate_task(list(NIFTY_50)), use_container_width=False
    )
    if _POPULATE.is_running():
        _POPULATE.poll()

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
