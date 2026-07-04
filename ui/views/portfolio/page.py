"""Portfolio page — PM cockpit: Overview · Positions · Risk · Flows over the tradebook.

Positions/holdings views are shared with MF Analysis (`ui.views.mutual_fund.*`).
"""

from __future__ import annotations

import polars as pl
import streamlit as st

from services.registry_service import load_registry
from ui.components.freshness_banner import clear_freshness_cache, is_fund_stale
from ui.state.loaders import load_holdings_data, load_txn_data
from ui.views.portfolio import flows_tab, overview_tab, positions_tab, risk_tab
from ui.views.portfolio.helpers import build_portfolio_value_series, get_mapped_data


def _refresh_portfolio(names: list[str], slugs: list[str]) -> None:
    """Refetch NAV + holdings for the portfolio's active funds and recompute metrics, in place.
    Stashes an outcome toast (shown after the rerun); a failing source is recorded, not fatal."""
    from data.repositories.holdings import refresh_holdings_data  # noqa: PLC0415 — defer off boot
    from data.repositories.nav import refresh_nav_data  # noqa: PLC0415
    from services.mf_metrics import recompute_metrics  # noqa: PLC0415

    failed: list[str] = []
    with st.spinner("Refreshing portfolio data…"):
        for label, step in (
            ("NAV", lambda: refresh_nav_data(names)),
            ("Holdings", lambda: refresh_holdings_data(slugs)),
            ("Metrics", lambda: recompute_metrics(names)),
        ):
            try:
                step()
            except Exception:
                failed.append(label)
    clear_freshness_cache()
    load_holdings_data.clear()
    st.session_state["_pf_refresh_msg"] = (
        ("⚠️", f"Refreshed with issues — {', '.join(failed)} failed.")
        if failed
        else ("✅", "Portfolio data refreshed — NAV, holdings & metrics updated.")
    )


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

    if is_fund_stale(active_names, active_slugs) and st.button(
        "🔄 Refresh portfolio data",
        key="pf_refresh",
        help="Some holdings have stale NAV/holdings — refetch them and recompute metrics now",
    ):
        _refresh_portfolio(active_names, active_slugs)
        st.rerun()

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
_pf_msg = st.session_state.pop("_pf_refresh_msg", None)
if _pf_msg:
    st.toast(_pf_msg[1], icon=_pf_msg[0])
_render(load_txn_data())
