"""MF Analysis · Benchmark tab — is this fund earning its keep vs its benchmark?

Cumulative excess return, cached CAPM stats, a rolling-alpha (alpha-decay) chart, and
any Overview alerts for this fund.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from services.benchmarks import SUBCATEGORY_BENCHMARK, resolve_benchmark_symbol
from services.mf_metrics import rolling_alpha
from ui.charts import theme
from ui.components.metric_tiles import Kpi, render_kpi_row
from ui.state.loaders import load_overview_alerts_cached
from ui.views.mutual_fund.context import load_index_returns

if TYPE_CHECKING:
    from ui.views.mutual_fund.context import FundContext

_ALPHA_WINDOW = 126  # ~6 months of trading days


def _resolve_benchmark(ctx: FundContext) -> tuple[str | None, str]:
    """Benchmark symbol + display label: metadata benchmark first, sub-category fallback."""
    symbol = resolve_benchmark_symbol(ctx.meta.get("benchmark"))
    if symbol:
        return symbol, ctx.meta.get("benchmark") or symbol
    if ctx.sub_category and ctx.sub_category in SUBCATEGORY_BENCHMARK:
        sym = SUBCATEGORY_BENCHMARK[ctx.sub_category]
        return sym, f"{sym} (sub-category benchmark)"
    return None, ""


def render(ctx: FundContext) -> None:
    symbol, label = _resolve_benchmark(ctx)
    if symbol is None:
        st.info(
            "No equity benchmark is mapped for this fund (typical for debt / arbitrage / "
            "liquid schemes) — benchmark-relative analytics don't apply."
        )
        return

    fund_dates = pd.DatetimeIndex(pd.to_datetime(ctx.nav_pd.index))
    bench_returns = load_index_returns(symbol, fund_dates.min().to_pydatetime(), fund_dates.max().to_pydatetime())
    if bench_returns.empty:
        st.warning(f"Could not load benchmark data for **{label}** (`{symbol}`).")
        return

    _render_capm_kpis(ctx)
    _render_excess_return(ctx, bench_returns, label)
    _render_alpha_decay(ctx, bench_returns, label)
    _render_fund_alerts(ctx)


def _render_capm_kpis(ctx: FundContext) -> None:
    m = ctx.metrics_row or {}
    st.caption("CAPM stats from the metrics cache (1Y daily returns vs the fund's category benchmark).")
    render_kpi_row(
        [
            Kpi("Alpha (1Y, ann.)", m.get("alpha_1y"), fmt="pct"),
            Kpi("Beta (1Y)", m.get("beta_1y"), fmt="ratio"),
            Kpi("R² (1Y)", m.get("r2_1y"), fmt="ratio"),
            Kpi("Tracking error (1Y)", m.get("tracking_error_1y"), fmt="pct_unsigned"),
        ]
    )


def _render_excess_return(ctx: FundContext, bench_returns: pd.Series, label: str) -> None:
    st.subheader(f"Cumulative excess return vs {label}")
    aligned = pd.concat([ctx.returns.rename("fund"), bench_returns.rename("bench")], axis=1, join="inner").dropna()
    if len(aligned) < 30:
        st.caption("Too little overlapping history for an excess-return curve.")
        return
    excess = ((1 + aligned["fund"]).cumprod() / (1 + aligned["bench"]).cumprod() - 1) * 100
    fig = go.Figure(
        go.Scatter(
            x=excess.index,
            y=excess.values,
            mode="lines",
            fill="tozeroy",
            line={"color": theme.ACCENT, "width": 2},
        )
    )
    fig.add_hline(y=0, line={"color": theme.NEUTRAL, "width": 1, "dash": "dot"})
    fig.update_layout(height=360, yaxis_title="Excess return %", hovermode="x")
    st.plotly_chart(fig, use_container_width=True, key="mf-bench-excess")
    st.caption("Above zero: ₹1 in the fund has outgrown ₹1 in the benchmark since the overlap start.")


def _render_alpha_decay(ctx: FundContext, bench_returns: pd.Series, label: str) -> None:
    st.subheader("Rolling alpha — is the edge decaying?")
    alpha = rolling_alpha(ctx.returns, bench_returns, window=_ALPHA_WINDOW)
    if alpha.empty:
        st.caption(f"Need more than {_ALPHA_WINDOW} overlapping trading days for rolling alpha.")
        return
    alpha_pct = alpha * 100
    fig = go.Figure(go.Scatter(x=alpha_pct.index, y=alpha_pct.values, mode="lines", line={"color": theme.INFO}))
    fig.add_hline(y=0, line={"color": theme.NEUTRAL, "width": 1, "dash": "dot"})
    fig.update_layout(height=300, yaxis_title=f"{_ALPHA_WINDOW}d Jensen's alpha (ann., %)", hovermode="x")
    st.plotly_chart(fig, use_container_width=True, key="mf-bench-alpha")
    latest = float(alpha_pct.iloc[-1])
    trend = "positive" if latest > 0 else "negative"
    st.caption(
        f"Rolling {_ALPHA_WINDOW}-day annualised alpha vs {label}. Latest: **{latest:+.1f}%** ({trend}). "
        "A long slide from positive to negative is the classic alpha-decay pattern."
    )


def _render_fund_alerts(ctx: FundContext) -> None:
    alerts = [a for a in load_overview_alerts_cached() if a.scheme_name == ctx.scheme_name]
    if not alerts:
        return
    st.subheader("Active alerts for this fund")
    box = {"critical": st.error, "warn": st.warning, "info": st.info}
    for a in alerts:
        box[a.severity](a.message)
