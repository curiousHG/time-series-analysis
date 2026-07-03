"""MF Analysis · About tab — fund facts in order of usefulness.

Costs/size and investment terms up front as tiles, exit-load prose collapsed, and the
identifiers/provenance rows (scheme codes, ISINs, slugs) tucked into an expander — they
matter for debugging, not for judging a fund.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

import pandas as pd
import streamlit as st

from mutual_funds.display import detect_option, detect_plan, make_slug
from ui.components.metric_tiles import Kpi, render_kpi_row

if TYPE_CHECKING:
    from ui.views.mutual_fund.context import FundContext


def _age_years(launch) -> float | None:
    if not launch:
        return None
    if isinstance(launch, str):
        try:
            launch = date.fromisoformat(launch[:10])
        except ValueError:
            return None
    if isinstance(launch, datetime):
        launch = launch.date()
    return (date.today() - launch).days / 365.25


def render(ctx: FundContext) -> None:
    meta, amfi_row, nav_pd = ctx.meta, ctx.amfi_row, ctx.nav_pd

    st.subheader("Costs & size")
    age = _age_years(meta.get("launchDate"))
    render_kpi_row(
        [
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
            Kpi("Turnover %", meta.get("turnoverRatio"), fmt="ratio", help="Portfolio churn per year."),
            Kpi(
                "Fund age",
                f"{age:.1f}y" if age is not None else None,
                help=f"Launched {meta.get('launchDate')}" if meta.get("launchDate") else None,
            ),
        ]
    )

    st.subheader("Investment terms")
    render_kpi_row(
        [
            Kpi("Min investment (₹)", meta.get("minInvestment"), fmt="inr"),
            Kpi("Min top-up (₹)", meta.get("minTopup"), fmt="inr"),
            Kpi("Plan", detect_plan(ctx.scheme_name) or "—"),
            Kpi("Option", detect_option(ctx.scheme_name)),
        ]
    )

    if meta.get("fundManager"):
        st.markdown(f"**Fund manager(s):** {meta['fundManager']}")

    exit_load = meta.get("exitLoad")
    if exit_load and exit_load != "—":
        # Often a paragraph of slab conditions — headline the short form, collapse the rest.
        if len(exit_load) > 120:
            with st.expander(f"Exit load — {exit_load[:90]}…"):
                st.write(exit_load)
        else:
            st.markdown(f"**Exit load:** {exit_load}")

    # ---- Data provenance, one quiet line
    st.divider()
    fetched = meta.get("fetchedAt")
    fetched_s = fetched.strftime("%d %b %Y") if isinstance(fetched, datetime) else "—"
    src = meta.get("sourceUrl") or ""
    src_host = "Kuvera" if "kuvera" in src else ("AdvisorKhoj" if "advisorkhoj" in src else "source")
    src_part = f"[{src_host}]({src})" if src else "—"
    st.caption(
        f"**Data:** NAV history {nav_pd.index.min():%d %b %Y} → {nav_pd.index.max():%d %b %Y} "
        f"({len(nav_pd):,} rows) · latest NAV ₹{nav_pd.iloc[-1]:.4f} · "
        f"metadata via {src_part}, fetched {fetched_s}"
    )

    with st.expander("Identifiers & raw fields"):
        rows = []
        if amfi_row:
            rows += [
                ("Scheme code (AMFI)", amfi_row["scheme_code"]),
                ("ISIN (Growth)", amfi_row["isin_growth"] or "—"),
                ("ISIN (Reinvestment)", amfi_row["isin_reinvestment"] or "—"),
                ("AMFI latest NAV", f"{amfi_row['nav']} ({amfi_row['nav_date']})"),
            ]
        rows += [
            ("Asset class", meta.get("assetClass") or "—"),
            ("Status", meta.get("status") or "—"),
            ("Slug (computed)", make_slug(ctx.scheme_name)),
        ]
        st.dataframe(pd.DataFrame(rows, columns=["Field", "Value"]), use_container_width=True, hide_index=True)
