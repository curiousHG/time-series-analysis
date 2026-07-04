"""Mutual Fund Analysis — single-fund deep dive.

Slim orchestrator: fund selection (selector), one data load into a FundContext, then
per-tab renderers (performance / benchmark / risk / holdings / calendar / about).
"""

from datetime import date

import polars as pl
import streamlit as st

from data.repositories.amfi import get_scheme_details_by_name
from data.repositories.nav import load_nav_df
from mutual_funds.display import make_slug, short_scheme_name
from services.mf_metrics import absolute_return
from services.registry_service import backfill_missing, list_tracked
from ui.components.freshness_banner import render_freshness_banner
from ui.components.metric_tiles import Kpi, render_kpi_row
from ui.state.loaders import load_metadata_cached, load_metrics_cached, load_txn_data
from ui.views.mutual_fund import about_tab, benchmark_tab, calendar_tab, holdings_tab, performance_tab, risk_tab
from ui.views.mutual_fund import selector as selector_view
from ui.views.mutual_fund.context import FundContext


def _held_position(scheme_name: str) -> dict | None:
    """Value / portfolio weight / XIRR of the user's stake in this fund, or None."""
    try:
        from services.portfolio_analytics import compute_xirr, fund_values_from_nav  # noqa: PLC0415 — off boot path
        from services.portfolio_service import get_mapped_data  # noqa: PLC0415

        txn = load_txn_data()
        result = get_mapped_data(txn) if txn is not None else None
        if result is None:
            return None
        mapped, portfolio_nav = result
        values = fund_values_from_nav(mapped, portfolio_nav)
        if scheme_name not in values:
            return None
        flows = (
            mapped.filter(pl.col("schemeName") == scheme_name)
            .with_columns(
                pl.when(pl.col("signed_qty") > 0)
                .then(pl.col("trade_value"))
                .otherwise(-pl.col("trade_value"))
                .alias("amount")
            )
            .select(pl.col("trade_date").alias("date"), "amount")
            .to_pandas()
        )
        as_of = portfolio_nav.filter(pl.col("schemeName") == scheme_name)["date"].max()
        return {
            "value": values[scheme_name],
            "weight_pct": values[scheme_name] / sum(values.values()) * 100,
            "xirr": compute_xirr(flows, values[scheme_name], as_of),
        }
    except Exception:
        return None


st.title("Mutual Fund Analysis")

# ---- Fund selection (pick among tracked funds; discovery lives on the MF Screener)
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

# ---- Load NAV first — the header shows live numbers, not just static identity
nav_df = load_nav_df([selected]).sort("date")
if nav_df.is_empty():
    st.warning("No NAV history for this fund.")
    st.stop()

nav_pd = nav_df.to_pandas().set_index("date")["nav"].astype(float).sort_index()

_metrics = load_metrics_cached().filter(pl.col("scheme_name") == selected)
_enriched_row = enriched.filter(pl.col("scheme_name") == selected)
_sub_category = _enriched_row["sub_category"][0] if _enriched_row.height else None

# ---- Header: who is this fund (identity line + chips), how is it doing (KPI row),
# ---- and what's YOUR stake in it (position strip).
amfi_row = get_scheme_details_by_name(selected)

meta_df = load_metadata_cached((selected,))
meta = meta_df.row(0, named=True) if meta_df.height else {}

amc = (meta.get("fundHouse") or (amfi_row["fund_house"] if amfi_row else None)) or "—"
benchmark = meta.get("benchmark")
launch = meta.get("launchDate")

held = _held_position(selected)
chips = [f":violet-background[**{_sub_category or meta.get('category') or 'Uncategorised'}**]"]
_RISK_CHIP_COLOR = {"Very High": "red", "High": "orange", "Moderately High": "orange", "Moderate": "blue"}
if meta.get("riskLevel"):
    _rc = _RISK_CHIP_COLOR.get(meta["riskLevel"], "green")
    chips.append(f":{_rc}-background[**{meta['riskLevel']} risk**]")
if held:
    chips.append(f":green-background[**Held · {held['weight_pct']:.1f}% of portfolio**]")

st.markdown(f"### {short_scheme_name(selected)}")
st.caption(selected)
st.markdown(" ".join(chips))
_identity_bits = [f"**{amc}**"]
if benchmark:
    _identity_bits.append(f"benchmarked to **{benchmark}**")
if launch:
    _age = (date.today() - launch).days / 365.25 if isinstance(launch, date) else None
    _identity_bits.append(f"launched {launch}" + (f" ({_age:.1f}y)" if _age else ""))
st.markdown(" · ".join(_identity_bits))

_chg_1d = float(nav_pd.iloc[-1] / nav_pd.iloc[-2] - 1) if len(nav_pd) >= 2 else None
render_kpi_row(
    [
        Kpi(
            "Latest NAV",
            f"₹{nav_pd.iloc[-1]:,.2f}",
            delta=f"{_chg_1d * 100:+.2f}%" if _chg_1d is not None else None,
            help=f"As of {nav_pd.index.max():%d %b %Y}",
        ),
        Kpi("1Y return", absolute_return(nav_pd, 252), fmt="pct"),
        Kpi(
            "AUM (₹ Cr)",
            meta.get("aumCrores") or None,
            fmt="inr",
            help=f"As of {meta.get('aumAsOf')}" if meta.get("aumAsOf") else None,
        ),
        Kpi(
            "TER %",
            meta.get("expenseRatio") or None,
            fmt="ratio",
            help=f"As of {meta.get('expenseRatioAsOf')}" if meta.get("expenseRatioAsOf") else None,
        ),
    ]
)
if held:
    xirr_part = f" · XIRR **{held['xirr'] * 100:+.1f}%**" if held.get("xirr") is not None else ""
    st.caption(f"Your position: **₹{held['value']:,.0f}**{xirr_part}")

ctx = FundContext(
    scheme_name=selected,
    short_name=short_scheme_name(selected),
    nav_pd=nav_pd,
    returns=nav_pd.pct_change().dropna(),
    meta=meta,
    amfi_row=amfi_row,
    metrics_row=_metrics.row(0, named=True) if _metrics.height else None,
    sub_category=_sub_category,
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
