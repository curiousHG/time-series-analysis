"""MF Analysis · Performance tab — period returns, rebased growth, rolling returns,
rolling-consistency vs benchmark, raw NAV."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from services.benchmarks import resolve_benchmark_symbol
from services.mf_metrics import absolute_return, rolling_beat_rate, windowed_cagr
from ui.charts import theme
from ui.components.metric_tiles import Kpi, fmt_pct, render_kpi_row
from ui.views.mutual_fund.context import load_index_returns, rebased_index

if TYPE_CHECKING:
    from ui.views.mutual_fund.context import FundContext

_RR_WINDOWS = {
    "3 Months": 63,
    "6 Months": 126,
    "1 Year": 252,
    "3 Years": 252 * 3,
    "5 Years": 252 * 5,
}
_CONSISTENCY_WINDOWS = {"1Y": ("rolling_1y", 252), "3Y": ("rolling_3y", 252 * 3), "5Y": ("rolling_5y", 252 * 5)}


def render(ctx: FundContext) -> None:
    nav_pd = ctx.nav_pd
    render_kpi_row(
        [
            Kpi("1M", absolute_return(nav_pd, 21), fmt="pct"),
            Kpi("3M", absolute_return(nav_pd, 63), fmt="pct"),
            Kpi("6M", absolute_return(nav_pd, 126), fmt="pct"),
            Kpi("1Y", absolute_return(nav_pd, 252), fmt="pct"),
            Kpi("3Y CAGR", windowed_cagr(nav_pd, 252 * 3), fmt="pct"),
            Kpi("5Y CAGR", windowed_cagr(nav_pd, 252 * 5), fmt="pct"),
        ]
    )

    _render_growth_chart(ctx)
    _render_rolling_returns(ctx)
    _render_consistency(ctx)

    st.subheader("NAV (raw)")
    fig_nav = go.Figure(go.Scatter(x=nav_pd.index, y=nav_pd.values, mode="lines", line={"color": theme.POSITIVE_SOFT}))
    fig_nav.update_layout(height=300, yaxis_title="NAV (₹)", hovermode="x")
    st.plotly_chart(fig_nav, use_container_width=True, key="mf-detail-nav")


def _render_growth_chart(ctx: FundContext) -> None:
    st.subheader("NAV growth (rebased to 100, vs benchmarks)")
    nav_pd = ctx.nav_pd

    fund_dates = pd.DatetimeIndex(pd.to_datetime(nav_pd.index))
    start_dt = fund_dates.min().to_pydatetime()
    end_dt = fund_dates.max().to_pydatetime()

    fig = go.Figure()
    fund_idx = nav_pd / nav_pd.iloc[0] * 100
    fig.add_trace(
        go.Scatter(x=fund_idx.index, y=fund_idx.values, name=ctx.short_name, mode="lines", line={"width": 2.5})
    )

    nifty_returns = load_index_returns("^NSEI", start_dt, end_dt)
    nifty_idx = rebased_index(nifty_returns, fund_dates)
    if not nifty_idx.empty:
        fig.add_trace(
            go.Scatter(
                x=nifty_idx.index,
                y=nifty_idx.values,
                name="Nifty 50",
                mode="lines",
                line={"dash": "dot", "color": theme.BENCHMARK_LINE},
            )
        )
    else:
        st.caption("⚠️ Nifty 50 data could not be loaded for this date range.")

    bench_symbol = resolve_benchmark_symbol(ctx.meta.get("benchmark"))
    if bench_symbol and bench_symbol != "^NSEI":
        bench_returns = load_index_returns(bench_symbol, start_dt, end_dt)
        bench_idx = rebased_index(bench_returns, fund_dates)
        if not bench_idx.empty:
            fig.add_trace(
                go.Scatter(
                    x=bench_idx.index,
                    y=bench_idx.values,
                    name=ctx.meta.get("benchmark") or bench_symbol,
                    mode="lines",
                    line={"dash": "dash", "color": theme.WARNING},
                )
            )
        else:
            st.caption(
                f"⚠️ Benchmark `{ctx.meta.get('benchmark')}` resolved to `{bench_symbol}` but no data could be fetched."
            )
    elif ctx.meta.get("benchmark") and not bench_symbol:
        st.caption(f"Benchmark `{ctx.meta.get('benchmark')}` has no fetchable symbol mapping. Showing Nifty 50 only.")

    fig.update_layout(
        height=480,
        hovermode="x unified",
        yaxis_title="Index (start = 100)",
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "xanchor": "right", "x": 1},
    )
    st.plotly_chart(fig, use_container_width=True, key="mf-detail-growth")


def _render_rolling_returns(ctx: FundContext) -> None:
    st.subheader("Rolling returns")
    nav_pd = ctx.nav_pd
    available_labels = [label for label, w in _RR_WINDOWS.items() if len(nav_pd) >= w + 30]
    if not available_labels:
        st.info("Need ≥ ~3 months of NAV history for rolling returns.")
        return

    win_label = st.radio(
        "Window",
        options=available_labels,
        index=min(2, len(available_labels) - 1),
        horizontal=True,
        key="mf-rolling-window",
    )
    win = _RR_WINDOWS[win_label]
    rr = nav_pd.pct_change(win, fill_method=None).dropna()
    # Annualise windows ≥ 252 days; otherwise show the period return as-is.
    if win >= 252:
        rr = (1 + rr) ** (252 / win) - 1
        ylabel = f"{win_label} CAGR %"
    else:
        ylabel = f"{win_label} return %"
    rr_pct = rr * 100

    render_kpi_row(
        [
            Kpi("Mean", rr.mean(), fmt="pct"),
            Kpi("Min", rr.min(), fmt="pct"),
            Kpi("Max", rr.max(), fmt="pct"),
            Kpi("Latest", rr.iloc[-1], fmt="pct"),
        ]
    )

    fig_rr = go.Figure()
    fig_rr.add_trace(go.Scatter(x=rr_pct.index, y=rr_pct.values, mode="lines", line={"color": theme.INFO}))
    fig_rr.add_hline(y=0, line={"color": theme.NEUTRAL, "width": 1, "dash": "dot"})
    fig_rr.update_layout(height=320, yaxis_title=ylabel, hovermode="x")
    st.plotly_chart(fig_rr, use_container_width=True, key="mf-detail-rolling")


def _render_consistency(ctx: FundContext) -> None:
    """Rolling-CAGR stats per window (from the metrics cache) + benchmark beat rate."""
    m = ctx.metrics_row
    if not m:
        return
    st.subheader("Rolling consistency")

    bench_symbol = resolve_benchmark_symbol(ctx.meta.get("benchmark"))
    bench_prices = pd.Series(dtype="float64")
    if bench_symbol:
        fund_dates = pd.DatetimeIndex(pd.to_datetime(ctx.nav_pd.index))
        bench_returns = load_index_returns(
            bench_symbol, fund_dates.min().to_pydatetime(), fund_dates.max().to_pydatetime()
        )
        if not bench_returns.empty:
            bench_prices = (1 + bench_returns).cumprod()

    rows = []
    for label, (prefix, days) in _CONSISTENCY_WINDOWS.items():
        if m.get(f"{prefix}_median") is None:
            continue
        beat = rolling_beat_rate(ctx.nav_pd, bench_prices, days) if not bench_prices.empty else None
        rows.append(
            {
                "Window": label,
                "Min": fmt_pct(m.get(f"{prefix}_min")),
                "Median": fmt_pct(m.get(f"{prefix}_median")),
                "Mean": fmt_pct(m.get(f"{prefix}_mean")),
                "Max": fmt_pct(m.get(f"{prefix}_max")),
                "Beat benchmark": f"{beat * 100:.0f}% of windows" if beat is not None else "—",
            }
        )
    if not rows:
        st.caption("Not enough history for rolling-window stats.")
        return
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    bench_note = ctx.meta.get("benchmark") or "—"
    st.caption(
        f"Rolling annualised CAGR across every window of the fund's history (cached metrics). "
        f"Beat rate = share of windows where the fund out-returned **{bench_note}**."
    )
