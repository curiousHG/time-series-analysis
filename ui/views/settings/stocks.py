"""Settings > Stock Data — data-health view + background (stale-while-revalidate) refresh.

Mirrors the richness of the Fund Data view: counts + last-updated + freshness badges for stocks
and indices. Refresh runs on a background task — the view keeps showing the last-cached data and
swaps to fresh once the task finishes (it polls while a task runs).
"""

from __future__ import annotations

import streamlit as st

from core.background import consume_if_finished, is_running, start_task
from services.stock_sync_service import (
    backfill_indices_history,
    list_unavailable_stocks,
    recompute_all_stock_metrics,
    refresh_all_stock_data,
    retry_stock_ohlcv,
    stock_data_health,
)
from ui.persistence.selections import load_selection, save_selection
from ui.state.loaders import load_stock_open_close, load_stock_screener_df_cached

_REFRESH_KEY = "stock_data_refresh"
_BACKFILL_KEY = "index_history_backfill"
_STALE_BDAYS = 2  # more than this many business days behind → stale


@st.cache_data(ttl=120, show_spinner=False)
def _health() -> dict:
    return stock_data_health()


def _clear_caches() -> None:
    _health.clear()
    load_stock_open_close.clear()
    load_stock_screener_df_cached.clear()


def _freshness_caption(col, last, stale_days: int | None) -> None:
    if last is None:
        col.caption(":gray-background[no data]")
    elif stale_days and stale_days > _STALE_BDAYS:
        col.caption(f":red-background[stale · {stale_days}bd behind] — {last}")
    else:
        col.caption(f":green-background[fresh] — {last}")


def render() -> None:
    st.markdown("### Stock & index data")

    # Consume any just-finished background task (stale-while-revalidate: swap to fresh + toast).
    for key, noun in ((_REFRESH_KEY, "Data refresh"), (_BACKFILL_KEY, "Index history backfill")):
        finished = consume_if_finished(key)
        if finished is None:
            continue
        _clear_caches()
        if finished.status == "done":
            r = finished.result
            summary = (
                f"{r.get('stock_rows', 0):,} stock + {r.get('index_rows', 0):,} index rows"
                if isinstance(r, dict)
                else f"{r:,} rows"
            )
            st.toast(f"{noun} done — {summary}.", icon="✅")
        else:
            st.toast(f"{noun} failed: {finished.error}", icon="⚠️")

    refreshing = is_running(_REFRESH_KEY)
    backfilling = is_running(_BACKFILL_KEY)
    if refreshing or backfilling:
        st.info("🔄 Running in the background — showing the last-cached data; this view updates when it's ready.")

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
    if b1.button("🔄 Refresh all data", type="primary", disabled=refreshing, use_container_width=True):
        start_task(_REFRESH_KEY, refresh_all_stock_data)
        st.rerun()
    if b2.button("Backfill full index history", disabled=backfilling, use_container_width=True):
        start_task(_BACKFILL_KEY, backfill_indices_history)
        st.rerun()
    if b3.button("Recompute metrics", disabled=refreshing, use_container_width=True):
        with st.spinner("Recomputing return / vol / CAPM alpha…"):
            n = recompute_all_stock_metrics()
        _clear_caches()
        st.toast(f"Recomputed metrics for {n:,} stocks.", icon="✅")
        st.rerun()

    # Poll while a background task runs so the view auto-updates the moment it finishes.
    if refreshing or backfilling:
        _poll_background()

    st.divider()
    _render_retry_unavailable()


@st.fragment(run_every="4s")
def _poll_background() -> None:
    if not is_running(_REFRESH_KEY) and not is_running(_BACKFILL_KEY):
        st.rerun(scope="app")  # a task just finished → rerun the page to consume it + show fresh data


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
