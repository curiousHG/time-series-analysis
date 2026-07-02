"""Portfolio snapshot for the Overview page — headline KPIs + 90-day value sparkline."""

from __future__ import annotations

from typing import TYPE_CHECKING

import streamlit as st

from services.portfolio_analytics import compute_xirr
from services.portfolio_service import build_portfolio_returns_series, get_signed_invested
from ui.charts import theme
from ui.components.metric_tiles import Kpi, fmt_inr_compact, render_kpi_row

if TYPE_CHECKING:
    import pandas as pd
    import polars as pl

SPARKLINE_DAYS = 90


def render(mapped: pl.DataFrame, portfolio_nav: pl.DataFrame, pv_df: pd.DataFrame) -> None:
    import plotly.graph_objects as go  # noqa: PLC0415 — heavy viz dep; deferred to render time
    import quantstats as qs  # noqa: PLC0415 — heavy dep; deferred to render time

    pv = pv_df.set_index("date")["portfolio_value"]
    current_value = float(pv.iloc[-1])
    as_of = pv.index[-1]

    signed = get_signed_invested(mapped)
    net_invested = float(signed["signed_invested"].sum())
    pnl = current_value - net_invested

    xirr = compute_xirr(
        signed.rename(columns={"signed_invested": "amount"}),
        terminal_value=current_value,
        terminal_date=as_of,
    )
    twr = build_portfolio_returns_series(mapped, portfolio_nav)
    twr_cagr = float(qs.stats.cagr(twr)) if len(twr) > 252 else None

    peak = float(pv.cummax().iloc[-1])
    curr_dd = (current_value / peak - 1) if peak > 0 else None

    st.subheader("Portfolio")
    st.caption(f"As of {as_of:%d %b %Y}")
    render_kpi_row(
        [
            Kpi("Value", fmt_inr_compact(current_value)),
            Kpi("Net invested", fmt_inr_compact(net_invested)),
            Kpi(
                "P&L",
                fmt_inr_compact(pnl),
                delta=f"{pnl / net_invested * 100:+.1f}%" if net_invested > 0 else None,
            ),
            Kpi("XIRR", xirr, fmt="pct", help="Money-weighted annual return of your actual cashflows."),
            Kpi(
                "CAGR (TWR)", twr_cagr, fmt="pct", help="Time-weighted — what the fund mix earned, SIP-timing removed."
            ),
            Kpi("Off peak", curr_dd, fmt="pct", help="Current drawdown from the portfolio's all-time high."),
        ]
    )

    recent = pv.iloc[-SPARKLINE_DAYS:]
    fig = go.Figure(
        go.Scatter(
            x=recent.index,
            y=recent.values,
            mode="lines",
            fill="tozeroy",
            line={"color": theme.ACCENT, "width": 2},
        )
    )
    fig.update_layout(
        height=160,
        margin={"l": 10, "r": 10, "t": 10, "b": 10},
        xaxis={"showgrid": False},
        yaxis={"visible": False, "rangemode": "normal"},
        showlegend=False,
    )
    fig.update_yaxes(range=[float(recent.min()) * 0.98, float(recent.max()) * 1.02])
    st.plotly_chart(fig, use_container_width=True, key="overview-sparkline", config={"displayModeBar": False})
