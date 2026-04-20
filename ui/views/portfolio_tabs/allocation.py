"""Allocation tab — holdings table, pie chart, P&L bar."""

import streamlit as st
import polars as pl
import pandas as pd
import plotly.express as px


def render(mapped: pl.DataFrame, nav_df: pl.DataFrame):
    alloc_rows = []
    for scheme in mapped.select("schemeName").unique().to_series().to_list():
        stxn = mapped.filter(pl.col("schemeName") == scheme)
        buys = stxn.filter(pl.col("signed_qty") > 0)
        sells = stxn.filter(pl.col("signed_qty") < 0)
        net_invested = buys["trade_value"].sum() - sells["trade_value"].sum()
        net_units = stxn["signed_qty"].sum()

        sn = nav_df.filter(pl.col("schemeName") == scheme).sort("date").tail(1)
        if sn.height > 0 and net_units > 0:
            current_nav = sn["nav"][0]
            current_value = net_units * current_nav
            pnl = current_value - net_invested
            pnl_pct = (pnl / net_invested * 100) if net_invested > 0 else 0
            alloc_rows.append({
                "Fund": scheme,
                "Invested": round(net_invested, 2),
                "Current Value": round(current_value, 2),
                "P&L": round(pnl, 2),
                "P&L %": round(pnl_pct, 2),
                "Units": round(net_units, 3),
                "NAV": round(current_nav, 4),
                "Allocation %": 0.0,
            })

    if not alloc_rows:
        st.info("No active holdings found.")
        return

    alloc_df = pd.DataFrame(alloc_rows)
    total_value = alloc_df["Current Value"].sum()
    total_invested = alloc_df["Invested"].sum()
    total_pnl = alloc_df["P&L"].sum()
    alloc_df["Allocation %"] = (alloc_df["Current Value"] / total_value * 100).round(1)
    alloc_df = alloc_df.sort_values("Current Value", ascending=False)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Invested", f"{total_invested:,.0f}")
    c2.metric("Current Value", f"{total_value:,.0f}")
    c3.metric("P&L", f"{total_pnl:,.0f}")
    c4.metric("P&L %", f"{total_pnl / total_invested * 100:.2f}%")

    st.dataframe(
        alloc_df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Invested": st.column_config.NumberColumn(format="%.2f"),
            "Current Value": st.column_config.NumberColumn(format="%.2f"),
            "P&L": st.column_config.NumberColumn(format="%.2f"),
            "P&L %": st.column_config.NumberColumn(format="%.2f%%"),
            "Allocation %": st.column_config.NumberColumn(format="%.1f%%"),
        },
    )

    col1, col2 = st.columns(2)
    with col1:
        fig_pie = px.pie(
            alloc_df, values="Current Value", names="Fund",
            title="Allocation by Value", hole=0.4,
        )
        fig_pie.update_traces(textposition="inside", textinfo="percent+label")
        fig_pie.update_layout(showlegend=False, height=400)
        st.plotly_chart(fig_pie, use_container_width=True, key="alloc-pie")

    with col2:
        fig_pnl = px.bar(
            alloc_df.sort_values("P&L"), x="P&L", y="Fund",
            orientation="h", title="P&L by Fund", color="P&L",
            color_continuous_scale=["#ef4444", "#fbbf24", "#10b981"],
        )
        fig_pnl.update_layout(height=400, showlegend=False)
        st.plotly_chart(fig_pnl, use_container_width=True, key="pnl-bar")
