"""Portfolio tab — orchestrates sub-tabs."""

import polars as pl
import streamlit as st

from data.repositories.registry import load_registry
from ui.components.freshness_banner import render_freshness_banner
from ui.views.portfolio_tabs import allocation, drawdown, fund_returns, growth, risk_metrics
from ui.views.portfolio_tabs.helpers import build_portfolio_value_series, get_mapped_data


def render(txn_df: pl.DataFrame | None):
    if txn_df is None:
        st.info("Upload a tradebook CSV from the Data Manager page.")
        return

    result = get_mapped_data(txn_df)
    if result is None:
        st.info("No fund mappings. Sync AMFI data and upload a tradebook in the Data Manager.")
        return

    mapped, portfolio_nav = result

    active_names = (
        mapped.group_by("schemeName")
        .agg(pl.col("signed_qty").sum().alias("units"))
        .filter(pl.col("units") > 0)
        .sort("schemeName")["schemeName"]
        .to_list()
    )
    registry = load_registry()
    name_to_slug = dict(zip(registry["schemeName"].to_list(), registry["schemeSlug"].to_list(), strict=False))
    active_slugs = [name_to_slug.get(n, "") for n in active_names]
    render_freshness_banner(active_names, active_slugs)

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
