"""Stock Analysis page — candlestick chart, fundamentals, and strategy backtest tabs."""

from __future__ import annotations

import pandas as pd
import polars as pl
import streamlit as st

from indicators import INDICATOR_REGISTRY, compute_indicators
from services.benchmarks import index_display_name
from stocks.constants import is_nse_exchange, to_bare_symbol
from ui.components import etf_detail, index_detail
from ui.components.chrome import page_header
from ui.components.notifications import render_toasts
from ui.state.loaders import (
    load_analysis_catalog_cached,
    load_global_search_cached,
    load_index_chart_ohlcv,
    load_stock_open_close,
    load_stock_screener_df_cached,
)
from ui.views.stock_analysis import chart as chart_tab
from ui.views.stock_analysis import fundamentals as fundamentals_tab
from ui.views.stock_analysis import strategy_backtest as backtest_tab


def _chart_pdf(frame: pl.DataFrame, sym: str) -> pd.DataFrame:
    """Slice one symbol out of an OHLCV frame into the chart's pandas shape."""
    return (
        frame.filter(pl.col("Symbol") == sym)
        .sort("Date")
        .with_columns(pl.col("Date").cast(pl.Utf8).alias("time"))
        .to_pandas()
    )


def _refresh_stock_on_open(ticker: str, frame: pl.DataFrame) -> None:
    """On the first open of a stock this session: pull its OHLCV forward to today (cheap no-op when
    already fresh) and make sure its CAPM/price metrics exist (compute if this is a brand-new stock).
    Reloads only if new bars arrived. Screener.in fundamentals are fetched by the Fundamentals tab."""
    import contextlib  # noqa: PLC0415

    from data.repositories.stock import refresh_stock_to_today  # noqa: PLC0415 — defer off boot
    from data.repositories.stock_fundamentals import load_stock_metrics  # noqa: PLC0415
    from services.stock_metrics import recompute_price_metrics  # noqa: PLC0415

    bare = to_bare_symbol(ticker)
    opened = st.session_state.setdefault("_sa_opened", set())
    if bare in opened:
        return
    opened.add(bare)
    shown = frame.filter(pl.col("Symbol") == bare)
    shown_max = shown.select(pl.col("Date").max()).item() if shown.height else None
    new_max = None
    metrics_added = False
    with st.spinner(f"Refreshing {ticker}…"):
        with contextlib.suppress(Exception):
            _, new_max = refresh_stock_to_today(bare)
        # Metrics: pick from cache if present, else compute now for this newly-opened stock.
        with contextlib.suppress(Exception):
            if load_stock_metrics([bare]).is_empty():
                recompute_price_metrics([bare])
                metrics_added = True
    if metrics_added:
        load_stock_screener_df_cached.clear()
    if new_max is not None and (shown_max is None or new_max > shown_max):
        load_stock_open_close.clear()
        st.rerun()


def _render_chart(sdf: pd.DataFrame, label: str) -> None:
    """Candle-interval control + indicator sidebar + chart. Shared by stocks and indices."""
    interval = st.segmented_control(
        "Candle interval", options=["Daily", "Weekly", "Monthly"], default="Daily", key="candle_interval"
    )
    chart_df = chart_tab.resample_ohlc(sdf, interval or "Daily")
    with st.sidebar:
        st.markdown("### Indicators")
        overlay_names = [n for n, e in INDICATOR_REGISTRY.items() if e["overlay"]]
        panel_names = [n for n, e in INDICATOR_REGISTRY.items() if not e["overlay"]]
        selected_overlays = st.multiselect(
            "Overlays (on price chart)", options=overlay_names, default=["SMA 20", "SMA 50"], key="selected_overlays"
        )
        selected_panels = st.multiselect("Panels (below chart)", options=panel_names, default=[], key="selected_panels")
    overlays, panels = compute_indicators(chart_df, selected_overlays + selected_panels)
    chart_tab.render(chart_df, overlays, panels, selected_panels, label)


def _stock_view(ticker: str, frame: pl.DataFrame, exchange: str | None = None) -> None:
    """Equity view: chart · fundamentals · backtest. Refreshes OHLCV on first open. `exchange`
    routes the fundamentals source (NSE → screener.in, international → yfinance)."""
    _refresh_stock_on_open(ticker, frame)
    sdf = _chart_pdf(frame, ticker)
    tab_chart, tab_fundamentals, tab_backtest = st.tabs(["Chart", "Fundamentals", "Strategy Backtest"])
    with tab_chart:
        _render_chart(sdf, ticker)
    with tab_fundamentals:
        fundamentals_tab.render(ticker, exchange=exchange)
    with tab_backtest:
        backtest_tab.render(sdf, ticker)


def _index_view(ticker: str) -> None:
    """Index view: valuation (P/E·P/B·Div-Yield·turnover) + trailing return + chart + constituents.
    Screener.in fundamentals / CAPM backtest are equity-only, so those tabs are replaced here."""
    label = index_display_name(ticker) if ticker.startswith("^") else ticker
    idf = load_index_chart_ohlcv(ticker, pd.to_datetime("2000-01-01"), pd.Timestamp.today().normalize())
    render_toasts()
    index_detail.render_valuation(ticker)
    if idf.is_empty():
        st.warning(f"No price history for **{label}** yet.")
        return
    index_detail.render_trailing(idf)
    tab_chart, tab_constituents = st.tabs(["Chart", "Constituents"])
    with tab_chart:
        _render_chart(_chart_pdf(idf, ticker), label)
    with tab_constituents:
        if ticker.startswith("^"):
            st.caption("Constituent breakdown isn't available for foreign indices.")
        else:
            index_detail.render_constituents(ticker)


def _etf_view(ticker: str, frame: pl.DataFrame) -> None:
    """ETF view: live NAV / premium-discount / underlying header + candlestick chart + backtest. ETFs
    trade like equities (OHLCV via the same path); screener fundamentals / CAPM don't apply."""
    _refresh_stock_on_open(ticker, frame)
    etf_detail.render_header(ticker)
    sdf = _chart_pdf(frame, ticker)
    tab_chart, tab_backtest = st.tabs(["Chart", "Strategy Backtest"])
    with tab_chart:
        _render_chart(sdf, ticker)
    with tab_backtest:
        backtest_tab.render(sdf, ticker)


# Unified ticker picker — one search across stocks, ETFs and indices (previously a confusing split).
# The selected item's kind drives which view renders; every ticker is loaded on demand.
_BADGE = {"stock": "Stock · NSE", "etf": "ETF · NSE", "index": "Index"}
_catalog = load_analysis_catalog_cached()
if not _catalog:
    st.info("Universe not synced yet — run **Sync NSE master** / **Sync ETF list** in Settings.")
    st.stop()
_KIND_OF = {c["id"]: c["kind"] for c in _catalog}
_NAME_OF = {c["id"]: c["name"] for c in _catalog}
_EXCH_OF = {c["id"]: c.get("exchange") for c in _catalog}


def _opt_label(cid: str) -> str:
    kind = _KIND_OF.get(cid, "stock")
    if kind == "index":
        disp = index_display_name(cid) if cid.startswith("^") else cid
        return f"{disp}  ·  Index"
    if kind == "etf":
        tag = "ETF"
    elif is_nse_exchange(_EXCH_OF.get(cid)):
        tag = "Stock"
    else:
        tag = f"Global · {_EXCH_OF.get(cid)}"
    name = _NAME_OF.get(cid) or ""
    suffix = f"  ·  {name[:40]}" if name and name != cid else ""
    return f"{cid}  ·  {tag}{suffix}"


def _render_add_international() -> None:
    """Search Yahoo Finance for any world ticker and register it into the universe. Once added it's
    a first-class stock: OHLCV on demand (as-is symbol), yfinance fundamentals, CAPM vs S&P 500."""
    with st.expander("🌍 Can't find it? Add an international ticker", expanded=False, icon=":material/public:"):
        q = st.text_input("Search Yahoo Finance", key="sa_intl_q", placeholder="e.g. apple, tesla, 7203.T, ASML.AS")
        if not q or len(q) < 2:
            return
        with st.spinner("Searching…"):
            results = load_global_search_cached(q)
        fresh = [r for r in results if r["symbol"] not in _KIND_OF and not r["symbol"].endswith(".NS")]
        if not fresh:
            st.caption("No new matches — NSE listings are already in the search box above.")
            return
        pick = st.selectbox(
            "Match",
            fresh,
            format_func=lambda r: f"{r['symbol']} — {r['name']}  ·  {r['exchange']}",
            key="sa_intl_pick",
        )
        if st.button("Add & open", type="primary", key="sa_intl_add"):
            from data.repositories.stock import register_stock  # noqa: PLC0415 — defer off boot

            register_stock(pick["symbol"], name=pick["name"], exchange=pick["exchange"], quote_type=pick["quote_type"])
            load_analysis_catalog_cached.clear()
            # The sa_ticker selectbox already rendered this run — a widget-keyed state can't be
            # written after instantiation. Stage the selection; it's applied pre-widget on rerun.
            st.session_state.sa_ticker_pending = pick["symbol"]
            st.toast(f"Added {pick['symbol']} ({pick['exchange']}).", icon="🌍")
            st.rerun()


_options = [c["id"] for c in _catalog]
_n_stock = sum(1 for c in _catalog if c["kind"] == "stock")
_n_etf = sum(1 for c in _catalog if c["kind"] == "etf")
_n_index = sum(1 for c in _catalog if c["kind"] == "index")
# A selection staged by the add-international flow (or navigation) — apply it BEFORE the selectbox
# instantiates; Streamlit forbids writing a widget's key after its widget rendered in the same run.
if (_pending := st.session_state.pop("sa_ticker_pending", None)) is not None:
    st.session_state.sa_ticker = _pending
if st.session_state.get("sa_ticker") not in _options:
    st.session_state.pop("sa_ticker", None)
ticker = st.selectbox(
    f"Search · {_n_stock:,} stocks · {_n_etf} ETFs · {_n_index} indices",
    _options,
    format_func=_opt_label,
    key="sa_ticker",
)
_render_add_international()
_kind = _KIND_OF.get(ticker, "stock")
_exchange = _EXCH_OF.get(ticker)
if _kind == "index" and ticker.startswith("^"):
    _disp = index_display_name(ticker)
else:
    _disp = ticker
_badge = _BADGE[_kind]
if _kind == "stock" and not is_nse_exchange(_exchange):
    _badge = f"Stock · {_exchange}"
page_header(_disp, _badge)

if _kind == "index":
    _index_view(ticker)
else:
    # Stock/ETF: load the selected ticker's history — fetched + stored on demand if we don't have it.
    with st.spinner(f"Loading {ticker}…"):
        frame = load_stock_open_close([ticker], pd.to_datetime("2000-01-01"), pd.Timestamp.today().normalize())
    render_toasts()
    if frame.is_empty():
        st.warning(f"Couldn't fetch any price data for **{ticker}** (it may be delisted or unlisted).")
        st.stop()
    if _kind == "etf":
        _etf_view(ticker, frame)
    else:
        _stock_view(ticker, frame, _exchange)
