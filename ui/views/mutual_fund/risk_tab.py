"""MF Analysis · Risk tab — 1Y/3Y ratios, vs-Nifty stats, drawdown, return distribution."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from services.benchmarks import resolve_benchmark_symbol
from services.mf_metrics import compute_tracking_error
from ui.charts import theme
from ui.components.metric_tiles import Kpi, render_kpi_row
from ui.constants import RF_DAILY
from ui.views.mutual_fund.context import load_index_returns

if TYPE_CHECKING:
    from ui.views.mutual_fund.context import FundContext


def render(ctx: FundContext) -> None:
    import quantstats as qs  # noqa: PLC0415 — heavy dep; deferred to render time

    nav_pd, returns = ctx.nav_pd, ctx.returns
    if len(returns) < 252:
        st.info(f"Need ≥ 1 year of NAV history for risk metrics — only {len(returns)} days available.")
        return

    last_year = returns.iloc[-252:]
    last_3y = returns.iloc[-min(252 * 3, len(returns)) :]

    def _q(fn, *a, **kw):
        try:
            return float(fn(*a, **kw))
        except Exception:
            return float("nan")

    sharpe_1y = _q(qs.stats.sharpe, last_year, rf=RF_DAILY)
    sortino_1y = _q(qs.stats.sortino, last_year, rf=RF_DAILY)
    vol_1y = _q(qs.stats.volatility, last_year)
    max_dd_1y = _q(qs.stats.max_drawdown, last_year)
    sharpe_3y = _q(qs.stats.sharpe, last_3y, rf=RF_DAILY)
    sortino_3y = _q(qs.stats.sortino, last_3y, rf=RF_DAILY)
    vol_3y = _q(qs.stats.volatility, last_3y)
    max_dd_all = _q(qs.stats.max_drawdown, returns)

    # Beta vs Nifty (1Y)
    nifty_r = load_index_returns(
        "^NSEI",
        nav_pd.index.min().to_pydatetime(),
        nav_pd.index.max().to_pydatetime(),
    )
    beta = alpha = r2 = float("nan")
    if not nifty_r.empty:
        common = pd.concat([last_year.rename("fund"), nifty_r.rename("nifty")], axis=1, join="inner").dropna()
        if len(common) >= 60 and common["nifty"].var() > 0:
            cov = common["fund"].cov(common["nifty"])
            var = common["nifty"].var()
            beta = cov / var
            alpha = common["fund"].mean() - beta * common["nifty"].mean()
            alpha = alpha * 252
            r2 = common["fund"].corr(common["nifty"]) ** 2

    st.subheader("1-year metrics")
    render_kpi_row(
        [
            Kpi("Sharpe", sharpe_1y, fmt="ratio"),
            Kpi("Sortino", sortino_1y, fmt="ratio"),
            Kpi("Volatility", vol_1y, fmt="pct_unsigned"),
            Kpi("Max drawdown", max_dd_1y, fmt="pct"),
        ]
    )

    st.subheader("3-year metrics")
    render_kpi_row(
        [
            Kpi("Sharpe", sharpe_3y, fmt="ratio"),
            Kpi("Sortino", sortino_3y, fmt="ratio"),
            Kpi("Volatility", vol_3y, fmt="pct_unsigned"),
            Kpi("Max DD (all-time)", max_dd_all, fmt="pct"),
        ]
    )

    st.subheader("vs Nifty 50 (1Y)")
    # Tracking error vs the fund's benchmark; falls back to Nifty if unmappable.
    bench_returns_for_te = nifty_r
    bench_label_for_te = "Nifty 50"
    bench_sym = resolve_benchmark_symbol(ctx.meta.get("benchmark"))
    if bench_sym and bench_sym != "^NSEI":
        br = load_index_returns(bench_sym, nav_pd.index.min().to_pydatetime(), nav_pd.index.max().to_pydatetime())
        if not br.empty:
            bench_returns_for_te = br
            bench_label_for_te = ctx.meta.get("benchmark") or bench_sym
    te = compute_tracking_error(ctx.scheme_name, bench_returns_for_te) if not bench_returns_for_te.empty else None

    ath = float(nav_pd.max())
    pct_from_ath = (float(nav_pd.iloc[-1]) / ath - 1) if ath > 0 else float("nan")

    render_kpi_row(
        [
            Kpi("Beta", beta, fmt="ratio"),
            Kpi("Alpha (annualised)", f"{alpha * 100:+.2f}%" if not np.isnan(alpha) else None),
            Kpi("R²", r2, fmt="ratio"),
            Kpi(f"Tracking error vs {bench_label_for_te}", f"{te * 100:.2f}%" if te is not None else None),
            Kpi("% from ATH", f"{pct_from_ath * 100:+.2f}%" if not np.isnan(pct_from_ath) else None),
        ]
    )

    st.subheader("Drawdown")
    cumulative = (1 + returns).cumprod()
    peak = cumulative.cummax()
    drawdown = (cumulative - peak) / peak * 100
    fig_dd = go.Figure(
        go.Scatter(
            x=drawdown.index, y=drawdown.values, fill="tozeroy", mode="lines", line={"color": theme.NEGATIVE_SOFT}
        )
    )
    fig_dd.update_layout(height=320, yaxis_title="Drawdown %", hovermode="x")
    st.plotly_chart(fig_dd, use_container_width=True, key="mf-detail-dd")

    st.subheader("Daily-return distribution")
    fig_dist = px.histogram(returns * 100, nbins=80, marginal="rug")
    fig_dist.update_layout(showlegend=False, height=320, xaxis_title="Daily return %")
    st.plotly_chart(fig_dist, use_container_width=True, key="mf-detail-dist")
