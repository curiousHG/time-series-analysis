"""MF Analysis · Calendar tab — monthly heatmap + calendar-year bars."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd
import plotly.express as px
import streamlit as st

if TYPE_CHECKING:
    from ui.views.mutual_fund.context import FundContext

_MONTH_ORDER = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def render(ctx: FundContext) -> None:
    nav_pd = ctx.nav_pd
    monthly = nav_pd.resample("ME").last().pct_change().dropna()
    if monthly.empty:
        st.info("Not enough data for calendar returns.")
        return

    df = pd.DataFrame({"return": monthly.values * 100, "date": monthly.index})
    df["year"] = df["date"].dt.year
    df["month"] = df["date"].dt.month_name().str[:3]
    pivot = df.pivot_table(index="year", columns="month", values="return", aggfunc="mean")
    pivot = pivot.reindex(columns=[m for m in _MONTH_ORDER if m in pivot.columns]).sort_index(ascending=False)

    st.subheader("Monthly returns heatmap (%)")
    fig_heat = px.imshow(
        pivot,
        color_continuous_scale="RdYlGn",
        color_continuous_midpoint=0,
        aspect="auto",
        text_auto=".1f",
    )
    fig_heat.update_layout(height=max(280, 28 * len(pivot)))
    st.plotly_chart(fig_heat, use_container_width=True, key="mf-detail-heat")

    st.subheader("Calendar-year returns (%)")
    yearly = nav_pd.resample("YE").last().pct_change().dropna() * 100
    if not yearly.empty:
        yearly_df = pd.DataFrame({"year": yearly.index.year, "return": yearly.values})
        fig_year = px.bar(
            yearly_df,
            x="year",
            y="return",
            color="return",
            color_continuous_scale="RdYlGn",
            color_continuous_midpoint=0,
            text_auto=".1f",
        )
        fig_year.update_layout(height=320, showlegend=False, yaxis_title="Return %")
        st.plotly_chart(fig_year, use_container_width=True, key="mf-detail-year")
