"""Settings > Stock Data — data-health view + background (stale-while-revalidate) refresh.

Mirrors the Fund Data view: counts + last-updated + freshness badges for stocks and indices.
Refresh + full-history backfill run on background tasks via the shared BackgroundRefresh component —
the view keeps showing the last-cached data and swaps to fresh once a task finishes.
"""

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
from ui.persistence.selections import load_selection, save_selection
from ui.state.loaders import load_stock_open_close, load_stock_screener_df_cached

_STALE_BDAYS = 2  # more than this many business days behind → stale


@st.cache_data(ttl=120, show_spinner=False)
def _health() -> dict:
    return stock_data_health()


def _clear_caches() -> None:
    _health.clear()
    load_stock_open_close.clear()
    load_stock_screener_df_cached.clear()


_REFRESH = BackgroundRefresh(
    "stock_data_refresh",
    "Data refresh",
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


def _freshness_caption(col, last, stale_days: int | None) -> None:
    if last is None:
        col.caption(":gray-background[no data]")
    elif stale_days and stale_days > _STALE_BDAYS:
        col.caption(f":red-background[stale · {stale_days}bd behind] — {last}")
    else:
        col.caption(f":green-background[fresh] — {last}")


def render() -> None:
    st.markdown("### Stock & index data")

    _GROUP.consume()  # swap-to-fresh + toast if a run just finished
    if _GROUP.any_running():
        _GROUP.poll()  # live banner/progress; full-rerun when done

    h = _health()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Stocks", f"{h['stocks']:,}")
    _freshness_caption(c1, h["stock_last"], h["stock_stale_days"])
    c2.metric("Indices", f"{h['indices']:,}")
    _freshness_caption(c2, h["index_last"], h["index_stale_days"])
    c3.metric("With price metrics", f"{h['with_metrics']:,}")
    c4.metric("With fundamentals", f"{h['with_fundamentals']:,}")
    if h["index_first"]:
        st.caption(f"Index history spans **{h['index_first']} → {h['index_last']}**.")
    if h["unavailable"]:
        st.caption(f"⚠️ **{h['unavailable']}** stock(s) marked unavailable — retry below.")

    b1, b2, b3 = st.columns(3)
    with b1:
        _REFRESH.start_button(
            "🔄 Refresh all data",
            lambda: refresh_all_stock_data(progress_cb=progress_cb(_REFRESH.key)),
            type="primary",
        )
    with b2:
        _BACKFILL.start_button(
            "Backfill full index history",
            lambda: backfill_indices_history(progress_cb=progress_cb(_BACKFILL.key)),
        )
    with b3:
        if st.button("Recompute metrics", disabled=_REFRESH.is_running(), use_container_width=True):
            with st.spinner("Recomputing return / vol / CAPM alpha…"):
                n = recompute_all_stock_metrics()
            _clear_caches()
            st.toast(f"Recomputed metrics for {n:,} stocks.", icon="✅")
            st.rerun()

    st.caption("Re-fetch replaces every stock's full history from yfinance (the single reliable source) — "
               "fixes any source-mixing corruption. Fundamentals scrapes screener.in for universe stocks that lack it.")
    b4, b5 = st.columns(2)
    with b4:
        _REPAIR.start_button(
            "🩺 Re-fetch all prices (yfinance)",
            lambda: refetch_all_stocks(progress_cb=progress_cb(_REPAIR.key)),
        )
    with b5:
        _FUNDA.start_button(
            "Fetch missing fundamentals",
            lambda: sync_missing_fundamentals(progress_cb=progress_cb(_FUNDA.key)),
        )

    st.divider()
    _render_retry_unavailable()


def _render_retry_unavailable() -> None:
    st.markdown("**Retry unavailable**")
    unavailable = list_unavailable_stocks()
    if not unavailable:
        st.caption("No stocks are marked unavailable — every tracked ticker has price history. 🎉")
        return
    st.caption(f"{len(unavailable)} symbol(s) with no downloadable price history.")
    pick = st.selectbox("Symbol", unavailable, key="stock_retry_pick")
    if st.button("Retry download", key="stock_retry_btn"):
        with st.spinner(f"Re-fetching {pick}…"):
            status = retry_stock_ohlcv(pick)
        if status == "available":
            watchlist = sorted({*load_selection("selected_stocks", []), pick})
            save_selection("selected_stocks", watchlist)
            load_stock_open_close.clear()
            st.success(f"**{pick}** recovered — re-added to your watchlist.")
        else:
            st.info(f"**{pick}** still has no data (status: {status}).")
        st.rerun()
