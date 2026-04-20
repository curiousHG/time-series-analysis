"""Portfolio tab — orchestrates sub-tabs."""

import streamlit as st
import polars as pl

from ui.views.portfolio_tabs.helpers import get_mapped_data, build_portfolio_value_series
from ui.views.portfolio_tabs import allocation, growth, drawdown, risk_metrics, fund_returns


def render(txn_df: pl.DataFrame | None):
    if txn_df is None:
        st.info("Upload a tradebook CSV from the Data Manager page.")
        return

    result = get_mapped_data(txn_df)
    if result is None:
        st.info("No fund mappings. Sync AMFI data and upload a tradebook in the Data Manager.")
        return

    mapped, portfolio_nav = result
    pv_series = build_portfolio_value_series(mapped, portfolio_nav)

    if pv_series is None or pv_series.empty:
        st.info("Not enough data to compute portfolio analytics.")
        return

    t_alloc, t_growth, t_drawdown, t_risk, t_returns = st.tabs(
        ["Allocation", "Growth", "Drawdown", "Risk Metrics", "Fund Returns"]
    )

    with t_alloc:
        allocation.render(mapped, portfolio_nav)

    with t_growth:
        growth.render(mapped, pv_series)

    with t_drawdown:
        drawdown.render(pv_series)

    with t_risk:
        risk_metrics.render(pv_series, mapped)

    with t_returns:
        fund_returns.render(mapped, portfolio_nav)
