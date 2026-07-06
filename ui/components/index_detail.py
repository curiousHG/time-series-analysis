"""Shared index-detail widgets — valuation, trailing performance, constituents. Used by both the
Overview → Indexes tab and the Stock Analysis index view so they stay in sync."""

from __future__ import annotations

import polars as pl
import streamlit as st

from ui.components.metric_tiles import EM_DASH
from ui.state.loaders import (
    load_index_constituents_cached,
    load_index_valuation_cached,
    load_stock_screener_df_cached,
)

_TRAILING = [("1D", 1), ("1W", 5), ("1M", 21), ("3M", 63), ("6M", 126), ("1Y", 252)]


def render_valuation(symbol: str) -> None:
    """Latest P/E · P/B · Div Yield · turnover for an NSE index (from the bhavcopy). Foreign `^`
    indices have none — the row is simply skipped."""
    val = load_index_valuation_cached(symbol)
    if not val:
        return
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("P/E", f"{val['pe']:.1f}" if val.get("pe") is not None else EM_DASH)
    c2.metric("P/B", f"{val['pb']:.2f}" if val.get("pb") is not None else EM_DASH)
    c3.metric("Div Yield", f"{val['div_yield']:.2f}%" if val.get("div_yield") is not None else EM_DASH)
    c4.metric("Turnover", f"₹{val['turnover_cr']:,.0f} Cr" if val.get("turnover_cr") is not None else EM_DASH)


def render_trailing(idf: pl.DataFrame) -> None:
    """Trailing 1D…1Y return tiles computed from an index's close series."""
    if idf.is_empty() or "Close" not in idf.columns:
        return
    closes = idf.sort("Date")["Close"].to_list()

    def _ret(n: int) -> float | None:
        if len(closes) < n + 1:
            return None
        start, end = closes[-(n + 1)], closes[-1]
        return (end / start - 1) if start else None

    st.markdown("**Trailing return**")
    for col, (label, n) in zip(st.columns(len(_TRAILING)), _TRAILING, strict=False):
        r = _ret(n)
        col.metric(label, f"{r * 100:+.1f}%" if r is not None else EM_DASH)


def render_constituents(index_name: str) -> None:
    """Constituent stocks of an NSE index (screener.in), enriched with universe metrics where held."""
    consts = load_index_constituents_cached(index_name)
    if not consts:
        return
    st.subheader(f"Constituents · {index_name}")
    universe = load_stock_screener_df_cached()
    if not universe.is_empty() and "symbol" in universe.columns:
        sub = universe.filter(pl.col("symbol").is_in(consts))
        cols = [c for c in ("symbol", "current_price", "return_1y", "alpha_1y", "stock_pe", "roe") if c in sub.columns]
        if sub.height and cols:
            st.dataframe(sub.select(cols).to_pandas(), use_container_width=True, hide_index=True, height=320)
            st.caption(f"{len(consts)} constituents (via screener.in).")
            return
    st.write(", ".join(consts))
    st.caption(f"{len(consts)} constituents (via screener.in).")
