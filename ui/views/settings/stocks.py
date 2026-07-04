"""Settings > Stock Data — refresh stock prices/metrics and retry failed downloads.

The efficient path is the NSE **bhavcopy**: one download carries every cash-market stock's OHLCV
for a day, so the whole universe is kept fresh in a handful of requests instead of one yfinance
call per symbol. Mirrors the MF "Retry unavailable" pattern for the per-symbol recovery case.
"""

from __future__ import annotations

import datetime as dt

import streamlit as st

from services.stock_sync_service import (
    list_unavailable_stocks,
    recompute_all_stock_metrics,
    refresh_stocks_via_bhavcopy,
    retry_stock_ohlcv,
)
from ui.persistence.selections import load_selection, save_selection
from ui.state.loaders import load_stock_open_close, load_stock_screener_df_cached


def render() -> None:
    st.markdown("### Stock price data")

    # --- Refresh all tracked stocks to today (NSE bhavcopy — one download per trading day) ---
    st.markdown("**Refresh prices**")
    st.caption(
        "Append the latest trading days for every tracked stock in a few downloads via the NSE "
        "bhavcopy (all stocks, one file per day)."
    )
    c1, c2 = st.columns(2)
    if c1.button("Update all to today", type="primary", key="stk_bhav_today", use_container_width=True):
        with st.spinner("Fetching NSE bhavcopy…"):
            n = refresh_stocks_via_bhavcopy()
        load_stock_open_close.clear()
        st.toast(f"Upserted {n:,} price rows from the bhavcopy.", icon="✅")
        st.rerun()
    if c2.button("Recompute stock metrics", key="stk_recompute", use_container_width=True):
        with st.spinner("Recomputing return / vol / CAPM alpha…"):
            n = recompute_all_stock_metrics()
        load_stock_screener_df_cached.clear()
        st.toast(f"Recomputed metrics for {n:,} stocks.", icon="✅")
        st.rerun()

    # --- A specific day's OHLCV for ALL stocks (not just tracked) ---
    with st.expander("Fetch a specific day's OHLCV for all stocks"):
        from data.repositories.stock import save_bhavcopy_day  # noqa: PLC0415 — defer off boot

        day = st.date_input("Trading day", value=dt.date.today() - dt.timedelta(days=1), key="stk_bhav_day")
        if st.button("Fetch bhavcopy for this day", key="stk_bhav_fetch"):
            with st.spinner(f"Fetching bhavcopy for {day}…"):
                n = save_bhavcopy_day(day, only_existing=False)
            load_stock_open_close.clear()
            if n:
                st.success(f"Stored OHLCV for {n:,} stocks on {day}.")
            else:
                st.info(f"No bhavcopy for {day} (holiday/weekend or not yet published).")

    st.divider()

    # --- Retry symbols with no fetchable price history ---
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
