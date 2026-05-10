"""Portfolio sub-tab — risk vs. return scatter for each active fund.

Two modes:
  • CAGR vs Volatility (default) — total-return view, asset-class agnostic.
  • Alpha vs Beta — CAPM lens against a user-selected benchmark; surfaces manager skill.

A gold "Portfolio" diamond marks the aggregate position. Bubble colour encodes allocation %
(uniform size) so small holdings stay equally readable.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta

import pandas as pd
import plotly.graph_objects as go
import polars as pl
import streamlit as st

from mutual_funds.display import short_scheme_name
from services.benchmarks import BENCHMARK_CHOICES, DEFAULT_BENCHMARK_LABEL
from services.mf_metrics import (
    compute_alpha_beta,
    compute_metrics_from_returns,
    load_cached_metrics,
    nav_series,
)
from services.portfolio_service import build_portfolio_returns_series
from ui.state.loaders import load_benchmark_returns

logger = logging.getLogger("ui.views.portfolio.risk_vs_return")

MODE_CAGR_VOL = "CAGR vs Volatility"
MODE_ALPHA_BETA = "Alpha vs Beta"

BUBBLE_SIZE = 14
PORTFOLIO_COLOUR = "#fbbf24"  # amber-400


def _fund_returns(scheme: str) -> pd.Series:
    nav = nav_series(scheme)
    return nav.pct_change().dropna() if not nav.empty else pd.Series(dtype="float64")


def render(mapped: pl.DataFrame, portfolio_nav: pl.DataFrame):
    """Plot each fund the user holds on a chosen risk/return plane."""
    st.subheader("Risk vs Return — your active funds")

    mode = st.segmented_control(
        "View",
        options=[MODE_CAGR_VOL, MODE_ALPHA_BETA],
        default=MODE_CAGR_VOL,
        key="risk-return-mode",
    )
    if mode is None:  # segmented_control returns None when nothing is selected
        mode = MODE_CAGR_VOL

    benchmark_label = DEFAULT_BENCHMARK_LABEL
    benchmark_symbol = BENCHMARK_CHOICES[DEFAULT_BENCHMARK_LABEL]
    if mode == MODE_ALPHA_BETA:
        benchmark_label = st.selectbox(
            "Benchmark",
            options=list(BENCHMARK_CHOICES.keys()),
            index=list(BENCHMARK_CHOICES.keys()).index(DEFAULT_BENCHMARK_LABEL),
            key="risk-return-benchmark",
        )
        benchmark_symbol = BENCHMARK_CHOICES[benchmark_label]
        st.caption(
            f"Alpha (Y) and Beta (X) computed against **{benchmark_label}** over the last ~1Y of overlapping "
            "trading days. Alpha is annualised. Funds with <60 overlapping days are greyed out."
        )
    else:
        st.caption(
            "X-axis: 1-year volatility (risk). Y-axis: 1-year CAGR (return). "
            "Bubble colour: portfolio allocation %. Top-left = best risk-adjusted return."
        )

    active_names = (
        mapped.group_by("schemeName")
        .agg(pl.col("signed_qty").sum().alias("units"))
        .filter(pl.col("units") > 0)["schemeName"]
        .to_list()
    )
    if not active_names:
        st.info("No active fund holdings to plot.")
        return

    # Latest NAV per fund → invested value per fund
    latest_nav = portfolio_nav.sort("date").group_by("schemeName").agg(pl.col("nav").last().alias("nav"))
    units_per_fund = mapped.group_by("schemeName").agg(pl.col("signed_qty").sum().alias("units"))
    value_df = (
        units_per_fund.join(latest_nav, on="schemeName", how="left")
        .with_columns((pl.col("units") * pl.col("nav")).alias("value"))
        .filter(pl.col("value") > 0)
    )
    value_by_name = dict(zip(value_df["schemeName"].to_list(), value_df["value"].to_list(), strict=False))

    # Date window for benchmark fetch — drawn from the portfolio's NAV span so we capture
    # at least the same range as the funds. Falls back to a 2-year window if NAV is empty.
    nav_dates = portfolio_nav.select("date").to_series()
    if nav_dates.len() > 0:
        start_dt = pd.to_datetime(nav_dates.min()).to_pydatetime()
        end_dt = pd.to_datetime(nav_dates.max()).to_pydatetime()
    else:
        end_dt = datetime.now()
        start_dt = end_dt - timedelta(days=730)

    # Pre-load benchmark returns once for alpha/beta mode. Errors surface as toasts
    # rather than being swallowed silently.
    bench_returns: pd.Series = pd.Series(dtype="float64")
    if mode == MODE_ALPHA_BETA:
        try:
            bench_returns = load_benchmark_returns(benchmark_symbol, start_dt, end_dt)
        except Exception as e:
            logger.exception("Benchmark fetch failed for %s (%s)", benchmark_label, benchmark_symbol)
            st.toast(f"Benchmark fetch failed for {benchmark_label} ({benchmark_symbol}): {e}", icon="⚠️")
            bench_returns = pd.Series(dtype="float64")
        if bench_returns.empty:
            logger.warning(
                "No benchmark data for %s (%s) in [%s, %s]; falling back to CAGR/Vol view",
                benchmark_label,
                benchmark_symbol,
                start_dt,
                end_dt,
            )
            st.toast(
                f"No data returned for {benchmark_label} ({benchmark_symbol}). Falling back to CAGR/Vol view.",
                icon="⚠️",
            )
            mode = MODE_CAGR_VOL

    # Single SELECT against mf_scheme_metrics for every active fund — replaces the per-fund
    # quantstats compute that used to fire on every Streamlit rerun.
    cached_metrics = load_cached_metrics(active_names)
    metrics_by_name: dict[str, dict] = {}
    if cached_metrics.height:
        for r in cached_metrics.iter_rows(named=True):
            metrics_by_name[r["scheme_name"]] = r

    rows: list[dict] = []
    skipped: list[str] = []
    insufficient: list[str] = []  # has history but <60 day overlap with chosen benchmark
    with st.spinner("Loading per-fund risk metrics…"):
        for name in active_names:
            m = metrics_by_name.get(name)
            if not m or m.get("vol_1y") is None or m.get("cagr_1y") is None:
                skipped.append(name)
                continue

            row = {
                "scheme": name,
                "label": short_scheme_name(name),
                "vol": m["vol_1y"] * 100,
                "cagr": m["cagr_1y"] * 100,
                "sharpe": m.get("sharpe_1y") or 0.0,
                "max_dd": (m.get("max_dd_1y") or 0.0) * 100,
                "value": value_by_name.get(name, 0.0),
                "alpha": math.nan,
                "beta": math.nan,
                "r2": math.nan,
                "has_capm": False,
            }

            if mode == MODE_ALPHA_BETA:
                try:
                    fr = _fund_returns(name)
                    last_year = fr.iloc[-252:] if len(fr) >= 252 else fr
                    ab = compute_alpha_beta(last_year, bench_returns)
                except Exception as e:
                    logger.exception("Alpha/Beta failed for %s vs %s", name, benchmark_symbol)
                    st.toast(f"Alpha/Beta failed for {short_scheme_name(name)}: {e}", icon="⚠️")
                    ab = None
                if ab is None:
                    insufficient.append(name)
                else:
                    row["alpha"] = ab["alpha"] * 100
                    row["beta"] = ab["beta"]
                    row["r2"] = ab["r2"]
                    row["has_capm"] = True

            rows.append(row)

    if not rows:
        st.info("Not enough NAV history (≥ 1 year) to compute risk/return for any active fund.")
        return

    df = pd.DataFrame(rows)
    total_invested = max(df["value"].sum(), 1.0)
    df["allocation"] = df["value"] / total_invested * 100.0

    # Portfolio aggregate metrics from the time-weighted (cash-flow-adjusted) return series.
    try:
        portfolio_point = _portfolio_metrics(mapped, portfolio_nav, mode, bench_returns)
    except Exception as e:
        logger.exception("Portfolio aggregate metrics failed")
        st.toast(f"Portfolio aggregate metrics failed: {e}", icon="⚠️")
        portfolio_point = None

    fig = go.Figure()

    if mode == MODE_ALPHA_BETA:
        _render_alpha_beta(fig, df, portfolio_point, benchmark_label)
    else:
        _render_cagr_vol(fig, df, portfolio_point)

    st.plotly_chart(fig, use_container_width=True, key="portfolio-risk-vs-return")

    _render_table(df, mode)

    if skipped:
        with st.expander(f"{len(skipped)} fund(s) excluded — insufficient NAV history"):
            for n in skipped:
                st.caption(f"• {short_scheme_name(n)}")
    if insufficient and mode == MODE_ALPHA_BETA:
        with st.expander(
            f"{len(insufficient)} fund(s) without alpha/beta — <60 overlapping days with {benchmark_label}"
        ):
            for n in insufficient:
                st.caption(f"• {short_scheme_name(n)}")


def _portfolio_metrics(
    mapped: pl.DataFrame,
    portfolio_nav: pl.DataFrame,
    mode: str,
    bench_returns: pd.Series,
) -> dict | None:
    """Compute the metric pair for the portfolio aggregate point. Returns None if unavailable.

    Uses the time-weighted (cash-flow-adjusted) return series — for a SIP-based portfolio,
    pct_change() of total value treats every contribution as a return and inflates CAGR.
    """
    pf_returns = build_portfolio_returns_series(mapped, portfolio_nav)
    if pf_returns.empty:
        return None

    base = compute_metrics_from_returns(pf_returns)
    point: dict = {
        "label": "Portfolio",
        "cagr": (base["cagr_1y"] * 100) if not math.isnan(base["cagr_1y"]) else math.nan,
        "vol": (base["vol_1y"] * 100) if not math.isnan(base["vol_1y"]) else math.nan,
        "sharpe": base["sharpe_1y"] if not math.isnan(base["sharpe_1y"]) else 0.0,
        "max_dd": (base["max_dd_1y"] * 100) if not math.isnan(base["max_dd_1y"]) else math.nan,
        "alpha": math.nan,
        "beta": math.nan,
        "r2": math.nan,
        "has_capm": False,
    }
    if mode == MODE_ALPHA_BETA and not bench_returns.empty:
        last_year = pf_returns.iloc[-252:] if len(pf_returns) >= 252 else pf_returns
        ab = compute_alpha_beta(last_year, bench_returns)
        if ab is not None:
            point["alpha"] = ab["alpha"] * 100
            point["beta"] = ab["beta"]
            point["r2"] = ab["r2"]
            point["has_capm"] = True
    return point


def _render_cagr_vol(fig: go.Figure, df: pd.DataFrame, portfolio: dict | None) -> None:
    vol_mid = float(df["vol"].median())
    cagr_mid = float(df["cagr"].median())

    xs = list(df["vol"])
    ys = list(df["cagr"])
    if portfolio and not math.isnan(portfolio["vol"]) and not math.isnan(portfolio["cagr"]):
        xs.append(portfolio["vol"])
        ys.append(portfolio["cagr"])
    x0, x1 = max(0.0, min(xs) - 2), max(xs) + 2
    y0, y1 = min(ys) - 2, max(ys) + 2

    # Quadrant shading
    fig.add_shape(
        type="rect",
        x0=x0,
        x1=vol_mid,
        y0=cagr_mid,
        y1=y1,
        fillcolor="rgba(134, 239, 172, 0.10)",
        line={"width": 0},
        layer="below",
    )
    fig.add_shape(
        type="rect",
        x0=vol_mid,
        x1=x1,
        y0=y0,
        y1=cagr_mid,
        fillcolor="rgba(252, 165, 165, 0.10)",
        line={"width": 0},
        layer="below",
    )
    fig.add_shape(
        type="rect",
        x0=x0,
        x1=vol_mid,
        y0=y0,
        y1=cagr_mid,
        fillcolor="rgba(253, 230, 138, 0.07)",
        line={"width": 0},
        layer="below",
    )
    fig.add_shape(
        type="rect",
        x0=vol_mid,
        x1=x1,
        y0=cagr_mid,
        y1=y1,
        fillcolor="rgba(253, 230, 138, 0.07)",
        line={"width": 0},
        layer="below",
    )

    fig.add_vline(x=vol_mid, line={"color": "#64748b", "dash": "dot", "width": 1})
    fig.add_hline(y=cagr_mid, line={"color": "#64748b", "dash": "dot", "width": 1})

    fig.add_trace(
        go.Scatter(
            x=df["vol"],
            y=df["cagr"],
            mode="markers+text",
            marker={
                "size": BUBBLE_SIZE,
                "color": df["allocation"],
                "colorscale": "Viridis",
                "cmin": 0,
                "showscale": True,
                "colorbar": {"title": "Allocation %", "thickness": 12, "len": 0.6},
                "line": {"color": "#1e293b", "width": 1},
                "opacity": 0.9,
            },
            text=df["label"],
            textposition="top center",
            textfont={"size": 11},
            customdata=df[["scheme", "max_dd", "value", "sharpe", "allocation"]],
            hovertemplate=(
                "<b>%{customdata[0]}</b><br>"
                "Allocation: %{customdata[4]:.1f}%<br>"
                "1Y CAGR: %{y:+.1f}%<br>"
                "1Y Vol: %{x:.1f}%<br>"
                "1Y Max DD: %{customdata[1]:+.1f}%<br>"
                "Sharpe: %{customdata[3]:.2f}<br>"
                "Invested value: ₹ %{customdata[2]:,.0f}<extra></extra>"
            ),
            name="Funds",
        )
    )

    _add_portfolio_marker(fig, portfolio, x_key="vol", y_key="cagr", mode=MODE_CAGR_VOL)

    # Quadrant labels
    fig.add_annotation(
        x=x0 + 0.5,
        y=y1 - 0.5,
        text="High return / Low risk",
        showarrow=False,
        font={"color": "#15803d", "size": 11},
        xanchor="left",
        yanchor="top",
    )
    fig.add_annotation(
        x=x1 - 0.5,
        y=y1 - 0.5,
        text="High return / High risk",
        showarrow=False,
        font={"color": "#a16207", "size": 11},
        xanchor="right",
        yanchor="top",
    )
    fig.add_annotation(
        x=x0 + 0.5,
        y=y0 + 0.5,
        text="Low return / Low risk",
        showarrow=False,
        font={"color": "#a16207", "size": 11},
        xanchor="left",
        yanchor="bottom",
    )
    fig.add_annotation(
        x=x1 - 0.5,
        y=y0 + 0.5,
        text="Low return / High risk",
        showarrow=False,
        font={"color": "#b91c1c", "size": 11},
        xanchor="right",
        yanchor="bottom",
    )

    fig.update_layout(
        height=560,
        xaxis_title="1Y Volatility (annualised, %)",
        yaxis_title="1Y CAGR (%)",
        xaxis={"range": [x0, x1]},
        yaxis={"range": [y0, y1]},
        showlegend=False,
    )


def _render_alpha_beta(fig: go.Figure, df: pd.DataFrame, portfolio: dict | None, bench_label: str) -> None:
    has = df[df["has_capm"]].copy()
    grey = df[~df["has_capm"]].copy()

    # Range: union of fund points and portfolio (if defined).
    all_betas = list(has["beta"]) if not has.empty else []
    all_alphas = list(has["alpha"]) if not has.empty else []
    if portfolio and portfolio.get("has_capm"):
        all_betas.append(portfolio["beta"])
        all_alphas.append(portfolio["alpha"])
    if not all_betas:
        all_betas = [0.0, 1.5]
    if not all_alphas:
        all_alphas = [-5.0, 5.0]
    x0, x1 = min(all_betas) - 0.15, max(all_betas) + 0.15
    y0, y1 = min(all_alphas) - 2, max(all_alphas) + 2
    # Make sure the β=1 and alpha=0 reference lines are in view.
    x0, x1 = min(x0, 0.5), max(x1, 1.3)
    y0, y1 = min(y0, -1.0), max(y1, 1.0)

    fig.add_vline(x=1.0, line={"color": "#64748b", "dash": "dot", "width": 1})
    fig.add_hline(y=0.0, line={"color": "#64748b", "dash": "dot", "width": 1})

    if not has.empty:
        fig.add_trace(
            go.Scatter(
                x=has["beta"],
                y=has["alpha"],
                mode="markers+text",
                marker={
                    "size": BUBBLE_SIZE,
                    "color": has["allocation"],
                    "colorscale": "Viridis",
                    "cmin": 0,
                    "showscale": True,
                    "colorbar": {"title": "Allocation %", "thickness": 12, "len": 0.6},
                    "line": {"color": "#1e293b", "width": 1},
                    "opacity": 0.9,
                },
                text=has["label"],
                textposition="top center",
                textfont={"size": 11},
                customdata=has[["scheme", "r2", "value", "allocation", "cagr", "vol"]],
                hovertemplate=(
                    "<b>%{customdata[0]}</b><br>"
                    "Allocation: %{customdata[3]:.1f}%<br>"
                    f"Alpha vs {bench_label}: "
                    "%{y:+.2f}%<br>"
                    f"Beta vs {bench_label}: "
                    "%{x:.2f}<br>"
                    "R²: %{customdata[1]:.2f}<br>"
                    "1Y CAGR: %{customdata[4]:+.1f}%<br>"
                    "1Y Vol: %{customdata[5]:.1f}%<br>"
                    "Invested value: ₹ %{customdata[2]:,.0f}<extra></extra>"
                ),
                name="Funds",
            )
        )

    if not grey.empty:
        # Place greyed-out funds at β=1, alpha=0 visual anchor with reduced opacity.
        fig.add_trace(
            go.Scatter(
                x=[1.0] * len(grey),
                y=[0.0] * len(grey),
                mode="markers",
                marker={
                    "size": BUBBLE_SIZE,
                    "color": "#94a3b8",
                    "opacity": 0.35,
                    "line": {"color": "#475569", "width": 1},
                },
                customdata=grey[["scheme"]],
                hovertemplate=(
                    f"<b>%{{customdata[0]}}</b><br>Insufficient overlap with {bench_label} (<60 days)<extra></extra>"
                ),
                name="Insufficient overlap",
            )
        )

    _add_portfolio_marker(fig, portfolio, x_key="beta", y_key="alpha", mode=MODE_ALPHA_BETA)

    # Quadrant labels — alpha/beta interpretation
    fig.add_annotation(
        x=x0 + 0.02,
        y=y1 - 0.5,
        text="Defensive winners (low β, + alpha)",
        showarrow=False,
        font={"color": "#15803d", "size": 11},
        xanchor="left",
        yanchor="top",
    )
    fig.add_annotation(
        x=x1 - 0.02,
        y=y1 - 0.5,
        text="Aggressive winners (high β, + alpha)",
        showarrow=False,
        font={"color": "#a16207", "size": 11},
        xanchor="right",
        yanchor="top",
    )
    fig.add_annotation(
        x=x0 + 0.02,
        y=y0 + 0.5,
        text="Defensive laggards (low β, - alpha)",
        showarrow=False,
        font={"color": "#a16207", "size": 11},
        xanchor="left",
        yanchor="bottom",
    )
    fig.add_annotation(
        x=x1 - 0.02,
        y=y0 + 0.5,
        text="Aggressive laggards (high β, - alpha)",
        showarrow=False,
        font={"color": "#b91c1c", "size": 11},
        xanchor="right",
        yanchor="bottom",
    )

    fig.update_layout(
        height=560,
        xaxis_title=f"Beta vs {bench_label}",
        yaxis_title=f"Alpha vs {bench_label} (annualised, %)",
        xaxis={"range": [x0, x1]},
        yaxis={"range": [y0, y1]},
        showlegend=False,
    )


def _add_portfolio_marker(fig: go.Figure, portfolio: dict | None, *, x_key: str, y_key: str, mode: str) -> None:
    if not portfolio:
        return
    if mode == MODE_ALPHA_BETA and not portfolio.get("has_capm"):
        return
    x = portfolio.get(x_key)
    y = portfolio.get(y_key)
    if x is None or y is None or (isinstance(x, float) and math.isnan(x)) or (isinstance(y, float) and math.isnan(y)):
        return

    if mode == MODE_ALPHA_BETA:
        hover = (
            "<b>Portfolio</b><br>"
            f"Alpha: {portfolio['alpha']:+.2f}%<br>"
            f"Beta: {portfolio['beta']:.2f}<br>"
            f"R²: {portfolio['r2']:.2f}<extra></extra>"
        )
    else:
        hover = (
            "<b>Portfolio</b><br>"
            f"1Y CAGR: {portfolio['cagr']:+.1f}%<br>"
            f"1Y Vol: {portfolio['vol']:.1f}%<br>"
            f"Sharpe: {portfolio['sharpe']:.2f}<extra></extra>"
        )

    fig.add_trace(
        go.Scatter(
            x=[x],
            y=[y],
            mode="markers+text",
            marker={
                "size": 22,
                "symbol": "diamond",
                "color": PORTFOLIO_COLOUR,
                "line": {"color": "#1e293b", "width": 2},
            },
            text=["Portfolio"],
            textposition="top center",
            textfont={"size": 12, "color": PORTFOLIO_COLOUR},
            hovertemplate=hover,
            name="Portfolio",
        )
    )


def _render_table(df: pd.DataFrame, mode: str) -> None:
    if mode == MODE_ALPHA_BETA:
        table = (
            df[["label", "allocation", "alpha", "beta", "r2", "cagr", "vol", "value"]]
            .rename(
                columns={
                    "label": "Fund",
                    "allocation": "Allocation %",
                    "alpha": "Alpha %",
                    "beta": "Beta",
                    "r2": "R²",
                    "cagr": "1Y CAGR %",
                    "vol": "1Y Vol %",
                    "value": "Invested (₹)",
                }
            )
            .sort_values("Allocation %", ascending=False)
        )
        st.dataframe(
            table,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Allocation %": st.column_config.NumberColumn(format="%.1f"),
                "Alpha %": st.column_config.NumberColumn(format="%+.2f"),
                "Beta": st.column_config.NumberColumn(format="%.2f"),
                "R²": st.column_config.NumberColumn(format="%.2f"),
                "1Y CAGR %": st.column_config.NumberColumn(format="%+.1f"),
                "1Y Vol %": st.column_config.NumberColumn(format="%.1f"),
                "Invested (₹)": st.column_config.NumberColumn(format="%,.0f"),
            },
        )
        return

    table = (
        df[["label", "allocation", "cagr", "vol", "sharpe", "max_dd", "value"]]
        .rename(
            columns={
                "label": "Fund",
                "allocation": "Allocation %",
                "cagr": "1Y CAGR %",
                "vol": "1Y Vol %",
                "sharpe": "Sharpe",
                "max_dd": "Max DD %",
                "value": "Invested (₹)",
            }
        )
        .sort_values("Allocation %", ascending=False)
    )
    st.dataframe(
        table,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Allocation %": st.column_config.NumberColumn(format="%.1f"),
            "1Y CAGR %": st.column_config.NumberColumn(format="%+.1f"),
            "1Y Vol %": st.column_config.NumberColumn(format="%.1f"),
            "Sharpe": st.column_config.NumberColumn(format="%.2f"),
            "Max DD %": st.column_config.NumberColumn(format="%+.1f"),
            "Invested (₹)": st.column_config.NumberColumn(format="%,.0f"),
        },
    )
