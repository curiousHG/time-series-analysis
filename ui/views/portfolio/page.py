"""Portfolio page — PM cockpit: Overview · Positions · Risk · Flows over the tradebook.

Positions/holdings views are shared with MF Analysis (`ui.views.mutual_fund.*`).
"""

from __future__ import annotations

import polars as pl
import streamlit as st

from services.registry_service import load_registry
from ui.components.background_refresh import BackgroundRefresh
from ui.components.freshness_banner import clear_freshness_cache, is_fund_stale
from ui.state.loaders import load_holdings_data, load_txn_data
from ui.views.portfolio import flows_tab, overview_tab, positions_tab, risk_tab
from ui.views.portfolio.helpers import build_portfolio_value_series, get_mapped_data

_REFRESH_KEY = "portfolio_refresh"


def _portfolio_refresh_task(names: list[str], slugs: list[str]) -> dict:
    """Refetch NAV + holdings for the portfolio's active funds and recompute metrics. Runs on a
    background task; a failing source is recorded, not fatal."""
    from core.background import set_task_progress  # noqa: PLC0415 — defer off boot
    from data.repositories.holdings import refresh_holdings_data  # noqa: PLC0415
    from data.repositories.nav import refresh_nav_data  # noqa: PLC0415
    from services.mf_metrics import recompute_metrics  # noqa: PLC0415

    steps = (
        ("NAV", lambda: refresh_nav_data(names)),
        ("Holdings", lambda: refresh_holdings_data(slugs)),
        ("Metrics", lambda: recompute_metrics(names)),
    )
    failed: list[str] = []
    for i, (label, step) in enumerate(steps):
        set_task_progress(_REFRESH_KEY, phase=label, done=i, total=len(steps))
        try:
            step()
        except Exception:
            failed.append(label)
    set_task_progress(_REFRESH_KEY, phase="Done", done=len(steps), total=len(steps))
    return {"failed": failed}


def _clear_portfolio_caches() -> None:
    clear_freshness_cache()
    load_holdings_data.clear()


_REFRESH = BackgroundRefresh(
    _REFRESH_KEY,
    "Portfolio refresh",
    cache_clearers=(_clear_portfolio_caches,),
    summarize=lambda r: (
        f"issues — {', '.join(r['failed'])} failed" if isinstance(r, dict) and r.get("failed") else "NAV, holdings & metrics updated"
    ),
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

    _REFRESH.consume()
    if _REFRESH.is_running():
        _REFRESH.poll()
    elif is_fund_stale(active_names, active_slugs):
        _REFRESH.start_button(
            "🔄 Refresh portfolio data",
            lambda: _portfolio_refresh_task(active_names, active_slugs),
            key="pf_refresh",
            help="Some holdings have stale NAV/holdings — refetch them and recompute metrics now",
            use_container_width=False,
        )

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
