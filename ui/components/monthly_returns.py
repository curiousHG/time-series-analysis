"""Monthly returns table: one year per row, one month per column, coloured by sign."""

from __future__ import annotations

from typing import TYPE_CHECKING

import quantstats as qs
import streamlit as st

if TYPE_CHECKING:
    import pandas as pd


def render_monthly_returns(returns: pd.Series, title: str = "Monthly returns (%)") -> None:
    st.subheader(title)
    monthly = qs.stats.monthly_returns(returns)
    if monthly is None or monthly.empty:
        st.caption("Not enough history for a monthly table.")
        return
    st.dataframe(
        (monthly * 100).style.format("{:.1f}").background_gradient(cmap="RdYlGn", axis=None),
        use_container_width=True,
    )
