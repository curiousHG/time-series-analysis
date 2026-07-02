"""Movers panel — biggest 1W / 1M moves among the user's held funds.

Fund-level rather than look-through stock-level: OHLCV coverage for the underlying
holdings is thin today, and the panel says what it shows.
"""

from __future__ import annotations

import streamlit as st

from ui.state.loaders import load_fund_movers_cached

TOP_N = 8


def render() -> None:
    st.subheader("Movers — your funds")
    movers = load_fund_movers_cached()
    if movers.is_empty():
        st.caption("No held funds with NAV history yet.")
        return

    pdf = movers.head(TOP_N).to_pandas()
    pdf = pdf[["fund", "weight_pct", "chg_1w", "chg_1m"]].rename(
        columns={"fund": "Fund", "weight_pct": "Weight %", "chg_1w": "1W", "chg_1m": "1M"}
    )
    st.dataframe(
        pdf,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Weight %": st.column_config.NumberColumn(format="%.1f%%"),
            "1W": st.column_config.NumberColumn(format="percent"),
            "1M": st.column_config.NumberColumn(format="percent"),
        },
    )
    st.caption("Sorted by absolute 1-week NAV move. Weight = share of portfolio value.")
