"""Settings → Stocks and indices: freshness in dates, the routine price refresh, and the
unavailable-symbol picker. Repair jobs live on the Maintenance tab.

Refresh and backfill run on background tasks via the shared BackgroundRefresh component — the view
keeps showing the last-cached data and swaps to fresh once a task finishes."""

from __future__ import annotations

import streamlit as st

from services.stock_sync_service import (
    backfill_indices_history,
    list_unavailable_stocks,
    recompute_all_stock_metrics,
    refetch_all_stocks,
    refresh_all_stock_data,
    retry_stock_ohlcv,
    stock_data_health,
    sync_missing_fundamentals,
)
from ui.components.background_refresh import BackgroundRefresh, BackgroundRefreshGroup, progress_cb
from ui.components.chrome import section_header
from ui.persistence.selections import load_selection, save_selection
from ui.state.loaders import load_analysis_catalog_cached, load_stock_open_close, load_stock_screener_df_cached
from ui.views.settings.format import fmt_date, plural

_STALE_BDAYS = 2


@st.cache_data(ttl=120, show_spinner=False)
def _health() -> dict:
    return stock_data_health()


def _clear_caches() -> None:
    _health.clear()
    load_stock_open_close.clear()
    load_stock_screener_df_cached.clear()
    load_analysis_catalog_cached.clear()


_REFRESH = BackgroundRefresh(
    "stock_data_refresh",
    "Price refresh",
    cache_clearers=(_clear_caches,),
    summarize=lambda r: (
        f"{r.get('stock_rows', 0):,} stock + {r.get('index_rows', 0):,} index rows"
        if isinstance(r, dict)
        else f"{r:,} rows"
    ),
)
_BACKFILL = BackgroundRefresh(
    "index_history_backfill",
    "Index history backfill",
    cache_clearers=(_clear_caches,),
    summarize=lambda r: f"{r:,} rows",
)
_REPAIR = BackgroundRefresh(
    "stock_ohlcv_refetch",
    "Price re-fetch",
    cache_clearers=(_clear_caches,),
    summarize=lambda r: f"{r:,} stocks re-fetched" if isinstance(r, int) else str(r),
)
_FUNDA = BackgroundRefresh(
    "stock_fundamentals_sync",
    "Fundamentals sync",
    cache_clearers=(_clear_caches,),
    summarize=lambda r: f"{r:,} symbols scraped" if isinstance(r, int) else str(r),
)
_GROUP = BackgroundRefreshGroup((_REFRESH, _BACKFILL, _REPAIR, _FUNDA))


def _freshness(last, stale_days: int | None) -> str:
    if last is None:
        return "no data"
    if stale_days and stale_days > _STALE_BDAYS:
        return f"as of {fmt_date(last)}, {plural(stale_days, 'business day')} behind"
    return f"as of {fmt_date(last)}"


def render_status() -> None:
    """The Stocks and indices block on the Data tab."""
    _GROUP.consume()
    actions = section_header("Stocks and indices")
    with actions:
        _REFRESH.start_button(
            "Refresh prices",
            lambda: refresh_all_stock_data(progress_cb=progress_cb(_REFRESH.key)),
            type="primary",
        )
    if _GROUP.any_running():
        _GROUP.poll()

    h = _health()
    st.markdown(
        f"{plural(h['stocks'], 'stock')} {_freshness(h['stock_last'], h['stock_stale_days'])}; "
        f"{plural(h['indices'], 'index').replace('indexs', 'indices')} {_freshness(h['index_last'], h['index_stale_days'])}."
    )
    st.caption(
        f"{h['with_metrics']:,} stocks have price metrics and {h['with_fundamentals']:,} have fundamentals"
        + (f". Index history starts {fmt_date(h['index_first'])}." if h["index_first"] else ".")
    )
    _render_unavailable()


def _render_unavailable() -> None:
    unavailable = list_unavailable_stocks()
    if not unavailable:
        return
    with st.expander(f"{plural(len(unavailable), 'symbol')} have no downloadable price history"):
        pick_col, go = st.columns([9, 3], vertical_alignment="bottom")
        pick = pick_col.selectbox("Symbol", unavailable, key="stock_retry_pick")
        if go.button("Retry", key="stock_retry_btn", use_container_width=True):
            with st.spinner(f"Re-fetching {pick}…"):
                status = retry_stock_ohlcv(pick)
            if status == "available":
                watchlist = sorted({*load_selection("selected_stocks", []), pick})
                save_selection("selected_stocks", watchlist)
                load_stock_open_close.clear()
                st.toast(f"{pick} recovered and added to your watchlist.", icon="✅")
            else:
                st.toast(f"{pick} still has no data (status: {status}).")
            st.rerun()


def _job(title: str, reason: str, button) -> None:
    label, action = st.columns([8, 4], vertical_alignment="center")
    label.markdown(f"**{title}**  \n{reason}")
    with action:
        button()


def render_maintenance() -> None:
    """Repair jobs on the Maintenance tab: rare, slow, and each with its reason."""
    _job(
        "Re-fetch all stock prices",
        "Replaces every stock's full history from yfinance. Use after a corrupted series is found.",
        lambda: _REPAIR.start_button(
            "Re-fetch prices", lambda: refetch_all_stocks(progress_cb=progress_cb(_REPAIR.key)), key="stk_refetch"
        ),
    )
    _job(
        "Backfill index history",
        "Pulls the full NSE index bhavcopy history back to the registry floor.",
        lambda: _BACKFILL.start_button(
            "Backfill indices",
            lambda: backfill_indices_history(progress_cb=progress_cb(_BACKFILL.key)),
            key="stk_backfill",
        ),
    )
    _job(
        "Fetch missing fundamentals",
        "Scrapes screener.in for universe stocks that have no fundamentals row yet.",
        lambda: _FUNDA.start_button(
            "Fetch fundamentals",
            lambda: sync_missing_fundamentals(progress_cb=progress_cb(_FUNDA.key)),
            key="stk_funda",
        ),
    )

    def _recompute() -> None:
        if st.button("Recompute metrics", key="stk_metrics", disabled=_REFRESH.is_running(), use_container_width=True):
            with st.spinner("Recomputing return, volatility and CAPM alpha…"):
                n = recompute_all_stock_metrics()
            _clear_caches()
            st.toast(f"Recomputed metrics for {n:,} stocks.", icon="✅")
            st.rerun()

    _job("Recompute stock metrics", "1Y return, volatility and CAPM alpha/beta for every stock.", _recompute)
