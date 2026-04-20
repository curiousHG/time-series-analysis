"""Risk Metrics tab — powered by quantstats, using time-weighted returns."""

import streamlit as st
import polars as pl
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import quantstats as qs


RISK_FREE = 0.065  # Indian risk-free rate


def render(pv: pd.DataFrame, mapped: pl.DataFrame):
    pv = pv.sort_values("date").copy()

    # Build time-weighted daily returns that remove the effect of cash flows.
    # On SIP days, the raw pct_change includes new money — we subtract it.
    cashflows = (
        mapped.with_columns(
            pl.when(pl.col("signed_qty") > 0)
            .then(pl.col("trade_value"))
            .otherwise(-pl.col("trade_value"))
            .alias("cashflow")
        )
        .group_by("trade_date")
        .agg(pl.sum("cashflow").alias("cashflow"))
        .rename({"trade_date": "date"})
        .to_pandas()
    )

    merged = pv.merge(cashflows, on="date", how="left")
    merged["cashflow"] = merged["cashflow"].fillna(0)

    # Time-weighted return: r_t = (V_t - V_{t-1} - CF_t) / V_{t-1}
    # Where CF_t is net cash flow on day t (positive = deposit, negative = withdrawal)
    merged["prev_value"] = merged["portfolio_value"].shift(1)
    merged["twr"] = (
        (merged["portfolio_value"] - merged["prev_value"] - merged["cashflow"])
        / merged["prev_value"]
    )
    merged = merged.dropna(subset=["twr"])

    # Clip extreme values caused by edge cases (first day, tiny denominator)
    merged["twr"] = merged["twr"].clip(-0.15, 0.15)

    returns = merged.set_index("date")["twr"]
    returns.index = pd.DatetimeIndex(returns.index)

    if len(returns) < 30:
        st.info("Need at least 30 days of data for risk metrics.")
        return

    _render_summary_metrics(returns)
    st.divider()
    _render_charts(returns, pv)


def _render_summary_metrics(returns: pd.Series):
    sharpe = qs.stats.sharpe(returns, rf=RISK_FREE / 252)
    sortino = qs.stats.sortino(returns, rf=RISK_FREE / 252)
    calmar = qs.stats.calmar(returns)
    gp = qs.stats.gain_to_pain_ratio(returns)
    vol = qs.stats.volatility(returns) * 100
    wr = qs.stats.win_rate(returns) * 100
    var_95 = qs.stats.var(returns) * 100
    cvar_95 = qs.stats.cvar(returns) * 100
    skew = qs.stats.skew(returns)
    kurt = qs.stats.kurtosis(returns)
    kelly = qs.stats.kelly_criterion(returns) * 100
    avg_w = qs.stats.avg_win(returns) * 100
    avg_l = qs.stats.avg_loss(returns) * 100
    payoff = abs(avg_w / avg_l) if avg_l != 0 else 0

    # Row 1 — return metrics
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("CAGR", f"{qs.stats.cagr(returns) * 100:.2f}%",
              help="Compound Annual Growth Rate (time-weighted, cash-flow adjusted). >15% is strong for Indian MFs. Compare against Nifty 50 CAGR (~12%).")
    c2.metric("Cumulative Return", f"{qs.stats.comp(returns) * 100:.2f}%",
              help="Total return since first investment, adjusted for cash flows. Shows pure investment performance.")
    c3.metric("Max Drawdown", f"{qs.stats.max_drawdown(returns) * 100:.2f}%",
              help="Largest peak-to-trough drop. <-10% is normal, <-20% is a significant correction, <-30% is a crash.")
    c4.metric("Avg Daily Return", f"{returns.mean() * 100:.3f}%",
              help="Average daily return. Positive is good. Multiply by 252 for rough annualized estimate.")

    # Row 2 — risk-adjusted
    c5, c6, c7, c8 = st.columns(4)
    c5.metric("Sharpe Ratio", f"{sharpe:.2f}",
              help=f"Return per unit of risk (vs {RISK_FREE*100}% risk-free). <0 = losing to FD, 0-1 = below average, 1-2 = good, >2 = excellent.")
    c6.metric("Sortino Ratio", f"{sortino:.2f}",
              help="Like Sharpe but only penalizes downside volatility. Higher is better. >1 is decent, >2 is strong.")
    c7.metric("Calmar Ratio", f"{calmar:.2f}",
              help="CAGR / Max Drawdown. Measures return per unit of crash risk. >1 is good, >3 is excellent.")
    c8.metric("Gain/Pain Ratio", f"{gp:.2f}",
              help="Sum of gains / sum of losses. >1 means gains exceed losses. >2 is strong momentum.")

    # Row 3 — volatility & tail risk
    c9, c10, c11, c12 = st.columns(4)
    c9.metric("Annual Volatility", f"{vol:.2f}%",
              help="Annualized standard deviation of returns. <10% is low (bonds), 10-20% is moderate (diversified MFs), >20% is high (small caps).")
    c10.metric("Win Rate", f"{wr:.1f}%",
               help="Percentage of positive days. >50% means more winning days than losing. 52-55% is typical for equities.")
    c11.metric("Best Day", f"{qs.stats.best(returns) * 100:.2f}%",
               help="Largest single-day gain. Shows upside potential. >3% is a significant rally.")
    c12.metric("Worst Day", f"{qs.stats.worst(returns) * 100:.2f}%",
               help="Largest single-day loss. Shows tail risk. <-3% is a significant drop.")

    # Row 4 — advanced
    c13, c14, c15, c16 = st.columns(4)
    c13.metric("Value at Risk (95%)", f"{var_95:.2f}%",
               help="95% of the time, your daily loss won't exceed this. E.g., -1.5% means on 95% of days you lose less than 1.5%.")
    c14.metric("CVaR (95%)", f"{cvar_95:.2f}%",
               help="Expected loss in the worst 5% of days. Worse than VaR -- shows average loss during tail events.")
    c15.metric("Skewness", f"{skew:.2f}",
               help="Return distribution asymmetry. Positive = more upside surprises, negative = more downside surprises. 0 is symmetric. -0.5 to 0.5 is normal.")
    c16.metric("Kurtosis", f"{kurt:.2f}",
               help="Tail thickness vs normal distribution. ~3 is normal. >5 means fat tails (more extreme moves). <3 means thinner tails.")

    # Row 5 — position sizing
    c17, c18, c19, c20 = st.columns(4)
    c17.metric("Kelly Criterion", f"{kelly:.1f}%",
               help="Optimal allocation % based on win rate and payoff. 20-50% is typical. Use half-Kelly for safety.")
    c18.metric("Avg Win", f"{avg_w:.3f}%",
               help="Average return on winning days. Compare with Avg Loss -- ideally should be higher.")
    c19.metric("Avg Loss", f"{avg_l:.3f}%",
               help="Average return on losing days. Smaller magnitude is better.")
    c20.metric("Payoff Ratio", f"{payoff:.2f}",
               help="Avg Win / Avg Loss. >1 means wins are larger than losses. Combined with win rate, determines profitability.")


def _render_charts(returns: pd.Series, pv: pd.DataFrame):
    col1, col2 = st.columns(2)

    with col1:
        fig_hist = go.Figure()
        fig_hist.add_trace(go.Histogram(
            x=returns * 100, nbinsx=80,
            marker_color="#6366f1", opacity=0.7, name="Daily Returns",
        ))
        fig_hist.add_vline(x=0, line_color="#94a3b8", line_dash="dash")
        fig_hist.add_vline(
            x=returns.mean() * 100, line_color="#f59e0b",
            annotation_text=f"Mean: {returns.mean() * 100:.3f}%",
        )
        var_95 = qs.stats.var(returns) * 100
        fig_hist.add_vline(
            x=var_95, line_color="#ef4444", line_dash="dot",
            annotation_text=f"VaR 95%: {var_95:.2f}%",
        )
        fig_hist.update_layout(
            height=350, title="Daily Returns Distribution (Cash-Flow Adjusted)",
            xaxis_title="Return (%)", yaxis_title="Count",
        )
        st.plotly_chart(fig_hist, use_container_width=True, key="returns-hist")

    with col2:
        trading_days = 252
        rolling_vol = returns.rolling(30).std() * np.sqrt(trading_days) * 100
        fig_vol = go.Figure()
        fig_vol.add_trace(go.Scatter(
            x=rolling_vol.index, y=rolling_vol,
            mode="lines", name="30d Rolling Vol",
            line=dict(color="#f59e0b", width=1.5),
            fill="tozeroy", fillcolor="rgba(245, 158, 11, 0.15)",
        ))
        fig_vol.update_layout(
            height=350, title="Rolling 30-Day Volatility",
            yaxis_title="Annualized Vol (%)", xaxis_title="Date",
        )
        st.plotly_chart(fig_vol, use_container_width=True, key="rolling-vol")

    # Rolling Sharpe
    rolling_sharpe = (
        (returns.rolling(60).mean() * 252 - RISK_FREE)
        / (returns.rolling(60).std() * np.sqrt(252))
    )
    fig_sharpe = go.Figure()
    fig_sharpe.add_trace(go.Scatter(
        x=rolling_sharpe.index, y=rolling_sharpe,
        mode="lines", name="60d Rolling Sharpe",
        line=dict(color="#6366f1", width=1.5),
    ))
    fig_sharpe.add_hline(y=0, line_dash="dash", line_color="#94a3b8")
    fig_sharpe.add_hline(y=1, line_dash="dot", line_color="#10b981", annotation_text="Sharpe = 1")
    fig_sharpe.update_layout(
        height=300, title="Rolling 60-Day Sharpe Ratio",
        yaxis_title="Sharpe", xaxis_title="Date",
    )
    st.plotly_chart(fig_sharpe, use_container_width=True, key="rolling-sharpe")

    # Monthly returns table
    st.subheader("Monthly Returns (%)")
    monthly = qs.stats.monthly_returns(returns)
    if monthly is not None and not monthly.empty:
        monthly_pct = monthly * 100
        st.dataframe(
            monthly_pct.style.format("{:.1f}").background_gradient(cmap="RdYlGn", axis=None),
            use_container_width=True,
        )
