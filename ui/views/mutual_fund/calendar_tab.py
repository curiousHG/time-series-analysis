"""MF Analysis · Calendar tab — monthly + calendar-year return bars."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd
import plotly.express as px
import streamlit as st

if TYPE_CHECKING:
    from ui.views.mutual_fund.context import FundContext


def render(ctx: FundContext) -> None:
    nav_pd = ctx.nav_pd
    monthly = nav_pd.resample("ME").last().pct_change().dropna()
    if monthly.empty:
        st.info("Not enough data for calendar returns.")
        return

    st.subheader("Monthly returns (%)")
    monthly_df = pd.DataFrame({"month": monthly.index, "return": monthly.values * 100})
    fig_month = px.bar(
        monthly_df,
        x="month",
        y="return",
        color="return",
        color_continuous_scale="RdYlGn",
        color_continuous_midpoint=0,
    )
    fig_month.update_layout(
        height=340, showlegend=False, coloraxis_showscale=False, xaxis_title=None, yaxis_title="Return %"
    )
    st.plotly_chart(fig_month, use_container_width=True, key="mf-detail-month")

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
