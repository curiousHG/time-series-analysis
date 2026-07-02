"""Mutual Fund Analysis — single-fund deep dive.

Slim orchestrator: fund selection (selector), one data load into a FundContext, then
per-tab renderers (performance / benchmark / risk / holdings / calendar / about).
"""

import polars as pl
import streamlit as st

from data.repositories.amfi import get_scheme_details_by_name
from data.repositories.nav import load_nav_df
from mutual_funds.display import detect_option, detect_plan, make_slug, short_scheme_name
from services.registry_service import backfill_missing, list_tracked
from ui.components.freshness_banner import render_freshness_banner
from ui.components.metric_tiles import Kpi, render_kpi_row
from ui.state.loaders import load_metadata_cached, load_metrics_cached
from ui.views.mutual_fund import about_tab, benchmark_tab, calendar_tab, holdings_tab, performance_tab, risk_tab
from ui.views.mutual_fund import selector as selector_view
from ui.views.mutual_fund.context import FundContext

st.title("Mutual Fund Analysis")

# ---- Fund selection (sidebar filters narrow the dropdown)
tracked = list_tracked()
if tracked.is_empty():
    st.info("No tracked funds yet. Add funds from the **MF Screener** page.")
    st.stop()

selected, enriched = selector_view.select_fund(tracked)
if selected is None:
    st.stop()

# ---- Auto-fetch any pending sources for this fund before rendering
reg_row = tracked.filter(pl.col("schemeName") == selected).row(0, named=True)
nav_status = reg_row["navStatus"]
holdings_status = reg_row["holdingsStatus"]
metadata_status = reg_row["metadataStatus"]

pending_sources = [
    s
    for s, status in [("nav", nav_status), ("metadata", metadata_status), ("holdings", holdings_status)]
    if status == "pending"
]

if pending_sources:
    with st.spinner(f"Fetching {' + '.join(pending_sources)} for **{short_scheme_name(selected)}**…"):
        backfill_missing(
            scheme_names=[selected],
            sources=tuple(pending_sources),
            max_per_run=len(pending_sources),
            submit_delay=0.0,  # single fund, no rate limiting needed
        )
    st.rerun()

# ---- Banners for sources confirmed unavailable
if nav_status == "unavailable":
    st.error(f"NAV data is **unavailable** for *{short_scheme_name(selected)}*. Try **Settings → Retry unavailable**.")
    st.stop()
if metadata_status == "unavailable":
    st.warning("Metadata not available for this fund — header AMC/AUM/TER/benchmark fields will be partial.")

# ---- Data-freshness banner for the selected fund
render_freshness_banner([selected], [make_slug(selected)])

# ---- Header — AMFI + metadata at a glance
amfi_row = get_scheme_details_by_name(selected)

meta_df = load_metadata_cached((selected,))
meta = meta_df.row(0, named=True) if meta_df.height else {}

st.markdown(f"### {short_scheme_name(selected)}")
st.caption(selected)

amc = (meta.get("fundHouse") or (amfi_row["fund_house"] if amfi_row else None)) or "—"
category = (meta.get("category") or (amfi_row["category"] if amfi_row else None)) or "—"
aum = meta.get("aumCrores")
ter = meta.get("expenseRatio")
benchmark = meta.get("benchmark") or "—"
launch = meta.get("launchDate")

render_kpi_row(
    [
        Kpi("AMC", amc),
        Kpi("Category", category),
        Kpi("AUM (₹ Cr)", aum or None, fmt="inr"),
        Kpi("TER %", ter or None, fmt="ratio"),
    ]
)
render_kpi_row(
    [
        Kpi("Plan", detect_plan(selected) or "—"),
        Kpi("Option", detect_option(selected)),
        Kpi("Benchmark", benchmark),
        Kpi("Launched", str(launch) if launch else None),
    ]
)

# ---- Load NAV + cached metrics into the shared context
nav_df = load_nav_df([selected]).sort("date")
if nav_df.is_empty():
    st.warning("No NAV history for this fund.")
    st.stop()

nav_pd = nav_df.to_pandas().set_index("date")["nav"].astype(float).sort_index()

_metrics = load_metrics_cached().filter(pl.col("scheme_name") == selected)
_enriched_row = enriched.filter(pl.col("scheme_name") == selected)

ctx = FundContext(
    scheme_name=selected,
    short_name=short_scheme_name(selected),
    nav_pd=nav_pd,
    returns=nav_pd.pct_change().dropna(),
    meta=meta,
    amfi_row=amfi_row,
    metrics_row=_metrics.row(0, named=True) if _metrics.height else None,
    sub_category=_enriched_row["sub_category"][0] if _enriched_row.height else None,
    holdings_status=holdings_status,
)

# ---- Tabs
tab_perf, tab_bench, tab_risk, tab_holdings, tab_calendar, tab_about = st.tabs(
    ["Performance", "vs Benchmark", "Risk", "Holdings", "Calendar", "About"]
)
with tab_perf:
    performance_tab.render(ctx)
with tab_bench:
    benchmark_tab.render(ctx)
with tab_risk:
    risk_tab.render(ctx)
with tab_holdings:
    holdings_tab.render(ctx)
with tab_calendar:
    calendar_tab.render(ctx)
with tab_about:
    about_tab.render(ctx)
