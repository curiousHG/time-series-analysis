"""Fund Returns tab — per-fund NAV growth, monthly heatmap."""

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import polars as pl
import streamlit as st

from ui.charts import theme


def render(mapped: pl.DataFrame, nav_df: pl.DataFrame):
    _render_fund_growth(mapped, nav_df)
    st.divider()
    _render_monthly_heatmap(nav_df)


def _render_fund_growth(mapped: pl.DataFrame, nav_df: pl.DataFrame):
    st.subheader("Fund NAV Growth (base=100 from first trade)")

    schemes = mapped.select("schemeName").unique().to_series().to_list()
    fig = go.Figure()
    colors = px.colors.qualitative.Set2

    for i, scheme in enumerate(schemes):
        sn = nav_df.filter(pl.col("schemeName") == scheme).sort("date").to_pandas()
        if len(sn) < 2:
            continue

        first_trade = mapped.filter(pl.col("schemeName") == scheme).sort("trade_date")["trade_date"].min()
        sn = sn[sn["date"] >= pd.Timestamp(first_trade)]
        if len(sn) < 2:
            continue

        first_nav = sn["nav"].iloc[0]
        sn["normalized"] = sn["nav"] / first_nav * 100

        fig.add_trace(
            go.Scatter(
                x=sn["date"],
                y=sn["normalized"],
                mode="lines",
                name=scheme[:40],
                line=dict(color=colors[i % len(colors)], width=1.5),
            )
        )

    fig.add_hline(y=100, line_dash="dash", line_color=theme.BENCHMARK_LINE)
    fig.update_layout(
        height=500,
        yaxis_title="Growth",
        xaxis_title="Date",
        hovermode="x unified",
        legend=dict(font=dict(size=10)),
    )
    st.plotly_chart(fig, use_container_width=True, key="fund-returns")


def _render_monthly_heatmap(nav_df: pl.DataFrame):
    st.subheader("Monthly Returns (%)")

    pv_all = nav_df.to_pandas()
    if pv_all.empty:
        return

    pivot = pv_all.pivot(index="date", columns="schemeName", values="nav")
    monthly = pivot.resample("ME").last().pct_change() * 100

    if monthly.empty or monthly.shape[0] < 2:
        st.info("Not enough data for monthly returns.")
        return

    monthly_avg = monthly.mean(axis=1)  # portfolio-average monthly return
    monthly_df = pd.DataFrame({"month": monthly_avg.index, "return": monthly_avg.values})
    fig = px.bar(
        monthly_df,
        x="month",
        y="return",
        color="return",
        color_continuous_scale="RdYlGn",
        color_continuous_midpoint=0,
    )
    fig.update_layout(
        height=320, showlegend=False, coloraxis_showscale=False, xaxis_title=None, yaxis_title="Return %"
    )
    st.plotly_chart(fig, use_container_width=True, key="monthly-heatmap")
