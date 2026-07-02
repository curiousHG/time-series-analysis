"""MF Analysis · Holdings tab — composition stats, holdings table, overlap vs portfolio."""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl
import streamlit as st

from data.repositories.holdings import load_assets, load_holdings, load_sectors
from mutual_funds.display import make_slug
from mutual_funds.holdings_stats import quick_stats
from services.portfolio_analytics import fund_values_from_nav, lookthrough_exposure
from services.portfolio_service import get_mapped_data
from services.registry_service import load_registry
from ui.components.metric_tiles import Kpi, render_kpi_row
from ui.components.mutual_fund_holdings import render_holdings_table
from ui.state.loaders import load_txn_data

if TYPE_CHECKING:
    from ui.views.mutual_fund.context import FundContext

_TOP_OVERLAP = 15


def render(ctx: FundContext) -> None:
    if ctx.holdings_status == "unavailable":
        st.warning("Holdings data is **unavailable** for this fund — common for ETFs and very new schemes.")
        return

    slug = make_slug(ctx.scheme_name)
    h = load_holdings([slug])
    s = load_sectors([slug])
    a = load_assets([slug])
    if h.is_empty():
        st.info("No holdings data fetched yet. Refresh from **Settings → Update All Holdings**.")
        return

    stats = quick_stats(slug)

    st.subheader("Asset & cap breakdown")
    render_kpi_row(
        [
            Kpi("% Equity", f"{stats['pct_equity']:.1f}%" if stats["pct_equity"] else None),
            Kpi("% Debt", f"{stats['pct_debt']:.1f}%" if stats["pct_debt"] else None),
            Kpi("% Cash", f"{stats['pct_cash']:.1f}%" if stats["pct_cash"] else None),
            Kpi("# Holdings", f"{stats['n_holdings']:,}"),
        ]
    )

    if stats["pct_largecap"] or stats["pct_midcap"] or stats["pct_smallcap"]:
        render_kpi_row(
            [
                Kpi("% Largecap", f"{stats['pct_largecap']:.1f}%"),
                Kpi("% Midcap", f"{stats['pct_midcap']:.1f}%"),
                Kpi("% Smallcap", f"{stats['pct_smallcap']:.1f}%"),
                Kpi("% Other (eq.)", f"{stats['pct_other_mcap']:.1f}%"),
            ]
        )

    st.subheader("Concentration")
    render_kpi_row(
        [
            Kpi("Top 3 holdings", f"{stats['pct_top3']:.1f}%" if stats["pct_top3"] is not None else None),
            Kpi("Top 5 holdings", f"{stats['pct_top5']:.1f}%" if stats["pct_top5"] is not None else None),
            Kpi("Top 10 holdings", f"{stats['pct_top10']:.1f}%" if stats["pct_top10"] is not None else None),
        ]
    )

    if any(stats["credit_breakdown"].values()):
        st.subheader("Credit-rating breakdown (debt slice)")
        render_kpi_row(
            [
                Kpi("% Sovereign", f"{stats['pct_sovereign']:.1f}%"),
                Kpi("% AAA / High-grade corp.", f"{stats['pct_corporate_high_grade']:.1f}%"),
                Kpi("% BBB & below (B-rated)", f"{stats['pct_b_rated']:.1f}%"),
                Kpi("% Poor / Unrated", f"{stats['pct_poor_rated']:.1f}%"),
            ]
        )

    st.divider()
    render_holdings_table(h, s, a, slug, display_name=ctx.short_name)

    st.divider()
    _render_portfolio_overlap(ctx, h)


def _render_portfolio_overlap(ctx: FundContext, fund_holdings: pl.DataFrame) -> None:
    """Would buying (more of) this fund add anything new to what you already own?"""
    st.subheader("Overlap with your portfolio")
    txn = load_txn_data()
    if txn is None:
        st.caption("Upload a tradebook in Settings to see how this fund overlaps your holdings.")
        return
    result = get_mapped_data(txn)
    if result is None:
        return
    mapped, portfolio_nav = result
    fund_values = fund_values_from_nav(mapped, portfolio_nav)
    if not fund_values:
        return

    registry = load_registry()
    name_to_slug = dict(zip(registry["schemeName"].to_list(), registry["schemeSlug"].to_list(), strict=False))
    slugs = [s for n in fund_values if (s := name_to_slug.get(n))]
    exposure = lookthrough_exposure(load_holdings(slugs), fund_values)
    if exposure.is_empty():
        st.caption("No holdings data for your active funds yet — refresh holdings from Settings.")
        return

    fund_h = fund_holdings.filter(pl.col("isin").is_not_null() & (pl.col("isin") != "")).select(
        pl.col("isin"),
        pl.col("instrumentName").alias("instrument_name_fund"),
        pl.col("weight").alias("fund_weight_pct"),
    )
    common = exposure.join(fund_h, on="isin", how="inner").sort("fund_weight_pct", descending=True)

    already_held = ctx.scheme_name in fund_values
    overlap_weight = float(common["fund_weight_pct"].sum()) if not common.is_empty() else 0.0
    if common.is_empty():
        st.success("No instrument overlap with your current look-through holdings — this fund adds new exposure.")
        return

    prefix = "You already hold this fund. " if already_held else ""
    st.caption(
        f"{prefix}**{overlap_weight:.0f}%** of this fund's portfolio overlaps instruments "
        f"you already own through your funds. Top overlaps:"
    )
    st.dataframe(
        common.head(_TOP_OVERLAP)
        .select(
            pl.col("instrument_name").alias("Instrument"),
            pl.col("fund_weight_pct").alias("Weight in this fund %"),
            pl.col("exposure_pct").alias("Already in your portfolio %"),
            pl.col("n_funds").alias("Via # funds"),
        )
        .to_pandas(),
        use_container_width=True,
        hide_index=True,
        column_config={
            "Weight in this fund %": st.column_config.NumberColumn(format="%.1f%%"),
            "Already in your portfolio %": st.column_config.NumberColumn(format="%.2f%%"),
        },
    )
