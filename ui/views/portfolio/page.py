"""Portfolio page — composes eight sub-tabs over the user's tradebook.

Holdings and overlap views are shared with MF Analysis (`ui.views.mutual_fund.*`).
"""

from __future__ import annotations

import polars as pl
import streamlit as st

from services.registry_service import load_registry
from ui.state.loaders import load_holdings_data, load_txn_data
from ui.views.mutual_fund import holdings, overlap
from ui.views.portfolio import allocation, drawdown, fund_returns, growth, risk_metrics, risk_vs_return
from ui.views.portfolio.helpers import build_portfolio_value_series, get_mapped_data


def _render(txn_df: pl.DataFrame | None) -> None:
    if txn_df is None:
        st.info("Upload a tradebook CSV from the Settings page.")
        return

    result = get_mapped_data(txn_df)
    if result is None:
        st.info("No fund mappings. Sync AMFI data and upload a tradebook in Settings.")
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
    active_slugs = [slug for n in active_names if (slug := name_to_slug.get(n))]
    active_registry = registry.filter(pl.col("schemeName").is_in(active_names))

    holdings_df, sectors_df, assets_df = load_holdings_data(active_slugs)
    pv_series = build_portfolio_value_series(mapped, portfolio_nav)
    if pv_series is None or pv_series.empty:
        st.info("Not enough data to compute portfolio analytics.")
        return

    t_alloc, t_growth, t_drawdown, t_risk, t_rvr, t_returns, t_overlap, t_holdings = st.tabs(
        [
            "Allocation",
            "Growth",
            "Drawdown",
            "Risk Metrics",
            "Risk vs Return",
            "Fund Returns",
            "Overlap & Allocation",
            "Holdings",
        ]
    )
    with t_alloc:
        allocation.render(mapped, portfolio_nav)
    with t_growth:
        growth.render(mapped, pv_series)
    with t_drawdown:
        drawdown.render(pv_series)
    with t_risk:
        risk_metrics.render(pv_series, mapped)
    with t_rvr:
        risk_vs_return.render(mapped, portfolio_nav)
    with t_returns:
        fund_returns.render(mapped, portfolio_nav)
    with t_overlap:
        overlap.render(holdings_df, sectors_df, active_registry)
    with t_holdings:
        holdings.render(holdings_df, sectors_df, assets_df, active_registry)


st.title("Portfolio")
_render(load_txn_data())
