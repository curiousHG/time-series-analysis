"""Portfolio · Flows tab — invested-over-time and per-fund NAV growth/heatmap."""

from __future__ import annotations

from typing import TYPE_CHECKING

import streamlit as st

from ui.views.portfolio import fund_returns, growth

if TYPE_CHECKING:
    import polars as pl


def render(mapped: pl.DataFrame, portfolio_nav: pl.DataFrame) -> None:
    growth.render_invested_over_time(mapped)
    st.divider()
    fund_returns.render(mapped, portfolio_nav)
