"""Portfolio page — PM cockpit: Overview · Positions · Risk · Flows over the tradebook.

Positions/holdings views are shared with MF Analysis (`ui.views.mutual_fund.*`).
"""

from __future__ import annotations

import polars as pl
import streamlit as st

from services.registry_service import load_registry
from ui.components.freshness_banner import render_freshness_banner
from ui.state.loaders import load_holdings_data, load_txn_data
from ui.views.portfolio import flows_tab, overview_tab, positions_tab, risk_tab
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

    render_freshness_banner(active_names, active_slugs)

    holdings_df, sectors_df, assets_df = load_holdings_data(active_slugs)
    pv_series = build_portfolio_value_series(mapped, portfolio_nav)
    if pv_series is None or pv_series.empty:
        st.info("Not enough data to compute portfolio analytics.")
        return

    t_overview, t_positions, t_risk, t_flows = st.tabs(["Overview", "Positions", "Risk", "Flows"])
    with t_overview:
        overview_tab.render(mapped, portfolio_nav, pv_series)
    with t_positions:
        positions_tab.render(mapped, portfolio_nav, holdings_df, sectors_df, assets_df, active_registry)
    with t_risk:
        risk_tab.render(mapped, portfolio_nav, pv_series)
    with t_flows:
        flows_tab.render(mapped, portfolio_nav)


st.title("Portfolio")
_render(load_txn_data())
