"""Allocation section — headline KPIs, per-fund positions table (with per-fund XIRR), allocation
donut and P&L bar."""

import pandas as pd
import plotly.graph_objects as go
import polars as pl
import streamlit as st

from mutual_funds.display import unique_short_names
from mutual_funds.tradebook import signed_flows
from services.portfolio_analytics import compute_xirr
from ui.charts import theme
from ui.components.metric_tiles import Kpi, fmt_inr_compact, render_kpi_row

_CHART_HEIGHT = 340


def _position_rows(mapped: pl.DataFrame, nav_df: pl.DataFrame) -> list[dict]:
    rows = []
    for scheme in mapped.select("schemeName").unique().to_series().to_list():
        stxn = mapped.filter(pl.col("schemeName") == scheme)
        net_invested = float(signed_flows(stxn)["amount"].sum())
        net_units = stxn["signed_qty"].sum()
        sn = nav_df.filter(pl.col("schemeName") == scheme).sort("date").tail(1)
        if sn.height == 0 or net_units <= 1e-6:
            continue
        current_nav = sn["nav"][0]
        current_value = net_units * current_nav
        pnl = current_value - net_invested
        xirr = compute_xirr(
            signed_flows(stxn).to_pandas(), terminal_value=float(current_value), terminal_date=sn["date"][0]
        )
        rows.append(
            {
                "Fund": scheme,
                "Invested": net_invested,
                "Current Value": current_value,
                "P&L": pnl,
                "P&L %": (pnl / net_invested * 100) if net_invested > 0 else None,
                "XIRR %": xirr * 100 if xirr is not None else None,
                "Units": net_units,
                "NAV": current_nav,
            }
        )
    return rows


def _net_invested(mapped: pl.DataFrame) -> float:
    return float(signed_flows(mapped)["amount"].sum())


def render(mapped: pl.DataFrame, nav_df: pl.DataFrame):
    rows = _position_rows(mapped, nav_df)
    if not rows:
        st.info("No active holdings found.")
        return

    alloc_df = pd.DataFrame(rows)
    total_value = float(alloc_df["Current Value"].sum())
    net_invested = _net_invested(mapped)
    total_pnl = total_value - net_invested
    alloc_df["Allocation %"] = alloc_df["Current Value"] / total_value * 100
    alloc_df = alloc_df.sort_values("Current Value", ascending=False)
    for money in ("Invested", "Current Value", "P&L"):
        alloc_df[money] = alloc_df[money].round(0)

    render_kpi_row(
        [
            Kpi("Value", fmt_inr_compact(total_value)),
            Kpi("Net invested", fmt_inr_compact(net_invested), help="Cash in minus cash out across every trade."),
            Kpi(
                "P&L",
                fmt_inr_compact(total_pnl),
                delta=f"{total_pnl / net_invested * 100:+.1f}%" if net_invested > 0 else None,
            ),
            Kpi("Funds held", len(alloc_df)),
        ]
    )

    st.dataframe(
        alloc_df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Fund": st.column_config.TextColumn(width="large"),
            "Invested": st.column_config.NumberColumn("Invested (₹)", format="localized"),
            "Current Value": st.column_config.NumberColumn("Value (₹)", format="localized"),
            "P&L": st.column_config.NumberColumn("P&L (₹)", format="localized"),
            "P&L %": st.column_config.NumberColumn(format="%+.1f%%"),
            "XIRR %": st.column_config.NumberColumn(
                format="%+.1f%%", help="Money-weighted annual return of this fund's own cashflows."
            ),
            "Units": st.column_config.NumberColumn(format="%.2f"),
            "NAV": st.column_config.NumberColumn(format="%.2f"),
            "Allocation %": st.column_config.NumberColumn("Weight %", format="%.1f%%"),
        },
    )

    labels = unique_short_names(alloc_df["Fund"])
    short = alloc_df["Fund"].map(labels)
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**Allocation by value**")
        fig_pie = go.Figure(
            go.Pie(
                labels=short,
                values=alloc_df["Current Value"],
                hole=0.55,
                textinfo="percent",
                textposition="inside",
                insidetextorientation="horizontal",
                marker={"colors": theme.COLORS * 2, "line": {"color": theme.BG_COLOR, "width": 1.5}},
                hovertemplate="%{label}<br>₹%{value:,.0f} · %{percent}<extra></extra>",
                sort=False,
            )
        )
        fig_pie.update_layout(
            height=_CHART_HEIGHT,
            margin={"l": 0, "r": 0, "t": 10, "b": 10},
            legend={"orientation": "v", "x": 1.02, "y": 0.5, "font": {"size": 11}},
            uniformtext={"minsize": 10, "mode": "hide"},
        )
        st.plotly_chart(fig_pie, use_container_width=True, key="alloc-pie")

    with col2:
        st.markdown("**P&L by fund**")
        ordered = alloc_df.sort_values("P&L")
        fig_pnl = go.Figure(
            go.Bar(
                x=ordered["P&L"],
                y=ordered["Fund"].map(labels),
                orientation="h",
                marker={"color": [theme.POSITIVE if v >= 0 else theme.NEGATIVE for v in ordered["P&L"]]},
                hovertemplate="%{y}<br>₹%{x:,.0f}<extra></extra>",
            )
        )
        fig_pnl.update_layout(
            height=_CHART_HEIGHT,
            margin={"l": 0, "r": 10, "t": 10, "b": 30},
            xaxis={"title": None, "tickprefix": "₹", "tickformat": "~s"},
            yaxis={"title": None, "tickfont": {"size": 11}},
            showlegend=False,
        )
        st.plotly_chart(fig_pnl, use_container_width=True, key="pnl-bar")
