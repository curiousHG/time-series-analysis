"""MF Analysis · About tab — full metadata dump for the selected fund."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

import pandas as pd
import streamlit as st

from mutual_funds.display import make_slug

if TYPE_CHECKING:
    from ui.views.mutual_fund.context import FundContext


def render(ctx: FundContext) -> None:
    rows = []
    amfi_row, meta, nav_pd = ctx.amfi_row, ctx.meta, ctx.nav_pd
    if amfi_row:
        rows.extend(
            [
                ("Scheme Code (AMFI)", amfi_row["scheme_code"]),
                ("ISIN (Growth)", amfi_row["isin_growth"] or "—"),
                ("ISIN (Reinvestment)", amfi_row["isin_reinvestment"] or "—"),
                ("AMFI Latest NAV", amfi_row["nav"]),
                ("AMFI NAV Date", amfi_row["nav_date"]),
            ]
        )
    if meta:
        rows.extend(
            [
                ("Asset Class", meta.get("assetClass") or "—"),
                ("Status", meta.get("status") or "—"),
                ("Fund Manager", meta.get("fundManager") or "—"),
                ("Launch Date", meta.get("launchDate") or "—"),
                ("AUM as of", meta.get("aumAsOf") or "—"),
                ("Expense Ratio as of", meta.get("expenseRatioAsOf") or "—"),
                ("Min Investment (₹)", meta.get("minInvestment")),
                ("Min Top-up (₹)", meta.get("minTopup")),
                ("Turnover Ratio %", meta.get("turnoverRatio")),
                ("Exit Load", meta.get("exitLoad") or "—"),
                ("Source URL", meta.get("sourceUrl") or "—"),
                (
                    "Metadata Fetched At",
                    fetched.strftime("%Y-%m-%d %H:%M")
                    if isinstance(fetched := meta.get("fetchedAt"), datetime)
                    else "—",
                ),
            ]
        )

    rows.extend(
        [
            ("Slug (computed)", make_slug(ctx.scheme_name)),
            ("NAV history days", len(nav_pd)),
            ("First NAV date", str(nav_pd.index.min().date())),
            ("Last NAV date", str(nav_pd.index.max().date())),
            ("Latest NAV", f"₹ {nav_pd.iloc[-1]:.4f}"),
        ]
    )

    about_df = pd.DataFrame(rows, columns=["Field", "Value"])
    st.dataframe(about_df, use_container_width=True, hide_index=True)
