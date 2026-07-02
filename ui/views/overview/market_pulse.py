"""Market pulse strip — Indian index levels and short-window changes."""

from __future__ import annotations

import streamlit as st

from ui.components.metric_tiles import EM_DASH, fmt_pct
from ui.state.loaders import load_market_pulse_cached


def render() -> None:
    pulse = load_market_pulse_cached()
    if pulse.is_empty():
        st.caption("Market data unavailable — sync index history from Stock Analysis.")
        return

    rows = pulse.to_dicts()
    cols = st.columns(len(rows))
    for col, r in zip(cols, rows, strict=False):
        delta = fmt_pct(r["chg_1d"], decimals=2)
        col.metric(
            r["label"],
            f"{r['last_close']:,.0f}",
            delta=delta if delta != EM_DASH else None,
            help=f"As of {r['as_of']}",
        )
        pos = f"{r['pos_52w'] * 100:.0f}% of 52w range" if r["pos_52w"] is not None else ""
        col.caption(
            f"1W {fmt_pct(r['chg_1w'])} · 1M {fmt_pct(r['chg_1m'])} · YTD {fmt_pct(r['chg_ytd'])}"
            + (f"<br>{pos}" if pos else ""),
            unsafe_allow_html=True,
        )
