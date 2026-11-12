"""Portfolio page: PM cockpit tabs Overview · Positions · Risk · Flows."""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl
import streamlit as st

from services.mf.registry import load_registry
from services.portfolio.value import build_portfolio_value_series, get_mapped_data
from ui.components.background_refresh import BackgroundRefresh, progress_cb
from ui.components.chrome import page_header
from ui.components.freshness_banner import clear_freshness_cache, is_fund_stale
from ui.state.loaders import clear_fund_data_caches, load_holdings_data, load_txn_data
from ui.views.portfolio import flows_tab, overview_tab, positions_tab, risk_tab

_REFRESH_KEY = "portfolio_refresh"


@dataclass(frozen=True, slots=True)
class PortfolioRefreshOutcome:
    """Labels of the refresh steps that raised."""

    failed: list[str]


def _portfolio_refresh_task(names: list[str], slugs: list[str]) -> PortfolioRefreshOutcome:
    """Refetch NAV + holdings for the portfolio's active funds and recompute metrics. Runs on a
    background task; a failing source is recorded, not fatal."""
    from services.mf.sync import refresh_funds  # noqa: PLC0415

    failed = refresh_funds(names, slugs, progress_cb=progress_cb(_REFRESH_KEY))
    return PortfolioRefreshOutcome(failed=failed)


def _clear_portfolio_caches() -> None:
    clear_freshness_cache()
    clear_fund_data_caches()


_REFRESH = BackgroundRefresh(
    _REFRESH_KEY,
    "Portfolio refresh",
    cache_clearers=(_clear_portfolio_caches,),
    summarize=lambda r: (f"issues — {', '.join(r.failed)} failed" if r.failed else "NAV, holdings & metrics updated"),
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
        .filter(pl.col("units") > 1e-6)
        .sort("schemeName")["schemeName"]
        .to_list()
    )
    registry = load_registry()
    name_to_slug = dict(zip(registry["schemeName"].to_list(), registry["schemeSlug"].to_list(), strict=False))
    active_slugs = [slug for n in active_names if (slug := name_to_slug.get(n))]
    active_registry = registry.filter(pl.col("schemeName").is_in(active_names))

    _REFRESH.consume()
    holdings_df, sectors_df, assets_df = load_holdings_data(active_slugs)
    pv_series = build_portfolio_value_series(mapped, portfolio_nav)
    stale = not _REFRESH.is_running() and is_fund_stale(active_names, active_slugs)

    def _refresh_action() -> None:
        if stale:
            _REFRESH.start_button(
                "",
                lambda: _portfolio_refresh_task(active_names, active_slugs),
                key="pf_refresh",
                icon=":material/refresh:",
                help="Some holdings have stale NAV or holdings. Refetch them and recompute metrics now.",
                use_container_width=False,
            )

    as_of = f"as of {pv_series['date'].iloc[-1]:%d %b %Y}" if pv_series is not None and not pv_series.empty else ""
    page_header("Portfolio", f"{len(active_names)} funds held{' · ' + as_of if as_of else ''}", actions=_refresh_action)
    if _REFRESH.is_running():
        _REFRESH.poll()
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


_render(load_txn_data())
