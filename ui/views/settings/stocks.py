"""Settings > Stock Data — retry price downloads that were marked unavailable.

Mirrors the MF "Retry unavailable" pattern (ui/views/settings/refresh.py) for the stock side:
symbols with no fetchable OHLCV get flagged unavailable + auto-removed from the watchlist; this
lets the user re-attempt them (e.g. after a transient outage or a corrected ticker).
"""

from __future__ import annotations

import streamlit as st

from services.stock_sync_service import list_unavailable_stocks, retry_stock_ohlcv
from ui.persistence.selections import load_selection, save_selection
from ui.state.loaders import load_stock_open_close


def render() -> None:
    st.markdown("### Stock price data")
    unavailable = list_unavailable_stocks()
    if not unavailable:
        st.caption("No stocks are marked unavailable — every tracked ticker has price history. 🎉")
        return

    st.caption(f"{len(unavailable)} symbol(s) with no downloadable price history. Retry to re-attempt a fetch.")
    pick = st.selectbox("Symbol", unavailable, key="stock_retry_pick")
    if st.button("Retry download", key="stock_retry_btn", type="primary"):
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
