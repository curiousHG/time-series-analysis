"""ETF-detail header — underlying, NAV, price vs NAV (premium/discount), 52-week range and 30d/1Y
performance, from the live NSE ETF metadata. Shown above the ETF's candlestick chart in Stock Analysis."""

from __future__ import annotations

import streamlit as st

from ui.components.metric_tiles import EM_DASH
from ui.state.loaders import load_etf_metadata_cached


def render_header(symbol: str) -> None:
    """NAV / price / premium-discount / 52-wk / 30d / 1Y tiles for an NSE ETF. Silent if the symbol
    isn't in the live ETF feed (nothing to show beyond the chart)."""
    meta = load_etf_metadata_cached().get(symbol)
    if not meta:
        return
    if meta.get("underlying"):
        st.caption(f"Tracks: **{meta['underlying']}**")

    nav, ltp = meta.get("nav"), meta.get("ltp")
    premium = (ltp / nav - 1) * 100 if nav and ltp else None
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("NAV", f"₹{nav:,.2f}" if nav is not None else EM_DASH)
    c2.metric("Market price", f"₹{ltp:,.2f}" if ltp is not None else EM_DASH)
    c3.metric(
        "Prem / Disc",
        f"{premium:+.2f}%" if premium is not None else EM_DASH,
        help="Market price vs NAV. Positive = trading above fair value (premium).",
    )
    c4.metric("30D", f"{meta['change_30d_pct']:+.1f}%" if meta.get("change_30d_pct") is not None else EM_DASH)
    c5.metric("1Y", f"{meta['change_365d_pct']:+.1f}%" if meta.get("change_365d_pct") is not None else EM_DASH)

    lo, hi = meta.get("week_low"), meta.get("week_high")
    if lo is not None and hi is not None:
        st.caption(f"52-week range: ₹{lo:,.2f} to ₹{hi:,.2f}")
