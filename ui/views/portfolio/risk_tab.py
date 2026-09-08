"""Portfolio · Risk tab — quantstats metrics, drawdown, risk-vs-return, fund correlation."""

from __future__ import annotations

from typing import TYPE_CHECKING

import streamlit as st

from mutual_funds.correlation_analytics import correlation_matrix, daily_returns, hierarchical_order
from mutual_funds.display import unique_short_names
from ui.charts.correlation_heatmap import render_correlation_heatmap
from ui.views.portfolio import drawdown, risk_metrics, risk_vs_return

if TYPE_CHECKING:
    import pandas as pd
    import polars as pl


def render(mapped: pl.DataFrame, portfolio_nav: pl.DataFrame, pv: pd.DataFrame) -> None:
    risk_metrics.render(pv, mapped)
    st.divider()
    drawdown.render(pv)
    st.divider()
    risk_vs_return.render(mapped, portfolio_nav)
    st.divider()
    _render_correlation(portfolio_nav)


def _render_correlation(portfolio_nav: pl.DataFrame) -> None:
    st.subheader("Fund correlation — are your funds actually diversified?")
    nav_pd = portfolio_nav.select(["date", "schemeName", "nav"]).to_pandas()
    nav_pd["schemeName"] = nav_pd["schemeName"].map(unique_short_names(nav_pd["schemeName"].unique()))
    nav_pd = nav_pd.drop_duplicates(subset=["date", "schemeName"])
    returns = daily_returns(nav_pd)
    corr = correlation_matrix(returns)
    if corr.empty:
        st.info("Need at least two funds with overlapping NAV history.")
        return
    order = hierarchical_order(corr)
    fig = render_correlation_heatmap(corr.loc[order, order])
    fig.update_layout(height=max(420, 34 * len(order)), title=None)
    st.plotly_chart(fig, use_container_width=True, key="pf-corr-heatmap")
    st.caption(
        "Daily-return correlation, hierarchically clustered. Blocks of deep red (> 0.9) "
        "mean those funds move together — overlapping bets, not diversification."
    )
