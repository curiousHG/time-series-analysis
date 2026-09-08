"""Portfolio · Overview tab — positions summary, growth vs benchmarks, contribution."""

from __future__ import annotations

from typing import TYPE_CHECKING

import streamlit as st

from mutual_funds.display import unique_short_names
from services.portfolio_analytics import contribution_analysis
from ui.views.portfolio import allocation, growth

if TYPE_CHECKING:
    import pandas as pd
    import polars as pl

_CONTRIB_WINDOWS = {"3M": 63, "6M": 126, "1Y": 252}


def render(mapped: pl.DataFrame, portfolio_nav: pl.DataFrame, pv: pd.DataFrame) -> None:
    allocation.render(mapped, portfolio_nav)
    st.divider()
    growth.render_growth_comparison(mapped, pv)
    st.divider()
    _render_contribution(mapped, portfolio_nav)


def _render_contribution(mapped: pl.DataFrame, portfolio_nav: pl.DataFrame) -> None:
    st.subheader("Return contribution — who actually made you money")
    window_label = st.radio("Window", options=list(_CONTRIB_WINDOWS), index=2, horizontal=True, key="pf-contrib-window")
    contrib = contribution_analysis(mapped, portfolio_nav, window_rows=_CONTRIB_WINDOWS[window_label])
    if contrib.empty:
        st.info(f"Need more than {window_label} of NAV history for contribution analysis.")
        return

    total = contrib["contribution_pct"].sum()
    st.caption(
        f"Fund P&L over the window ÷ portfolio start value. Sums to the portfolio's "
        f"{window_label} return (**{total:+.1f}%**); intra-window flows make it approximate."
    )
    display = contrib.assign(
        fund=contrib["fund"].map(unique_short_names(contrib["fund"])), pnl=contrib["pnl"].round(0)
    ).rename(
        columns={
            "fund": "Fund",
            "weight_start_pct": "Start weight %",
            "pnl": "P&L (₹)",
            "contribution_pct": "Contribution %",
            "fund_return_pct": "Fund return %",
        }
    )
    st.dataframe(
        display,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Start weight %": st.column_config.NumberColumn(format="%.1f%%"),
            "P&L (₹)": st.column_config.NumberColumn(format="localized"),
            "Contribution %": st.column_config.NumberColumn(format="%+.2f%%"),
            "Fund return %": st.column_config.NumberColumn(format="%+.1f%%"),
        },
    )
