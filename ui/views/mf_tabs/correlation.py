"""Correlation tab — clustered, market-stripped, downside-aware."""

import pandas as pd
import streamlit as st

from mutual_funds.correlation_analytics import (
    correlation_matrix,
    daily_returns,
    downside_returns,
    excess_returns,
    hierarchical_order,
    monthly_returns,
    rolling_pair_corr,
    top_pair,
)
from ui.charts.correlation_views import plot_clustered_heatmap, plot_rolling_corr
from ui.state.loaders import load_nifty_returns


def render(nav_pd: pd.DataFrame):
    if nav_pd.empty:
        st.info("No NAV data available.")
        return

    daily = daily_returns(nav_pd)
    if daily.shape[1] < 2:
        st.info("Need at least 2 funds for correlation analysis.")
        return

    monthly = monthly_returns(nav_pd)

    start = pd.to_datetime(daily.index.min())
    end = pd.to_datetime(daily.index.max())
    nifty = load_nifty_returns(start, end)
    has_nifty = not nifty.empty

    daily_dt = daily.copy()
    daily_dt.index = pd.to_datetime(daily_dt.index)

    excess = excess_returns(daily_dt, nifty) if has_nifty else pd.DataFrame()
    down = downside_returns(daily_dt, nifty) if has_nifty else pd.DataFrame()

    corr_daily = correlation_matrix(daily)
    corr_monthly = correlation_matrix(monthly, min_periods=12)
    corr_excess = correlation_matrix(excess) if has_nifty else pd.DataFrame()
    corr_down = correlation_matrix(down, min_periods=15) if has_nifty else pd.DataFrame()

    base_for_order = corr_excess if not corr_excess.empty else corr_daily
    ordered = hierarchical_order(base_for_order)

    with st.expander("How to read these views", expanded=False):
        st.markdown(
            """
- **Daily** — raw daily return correlation. Indian equity funds usually all sit at 0.85+ here because they share broad-market beta. High values are *expected* and not very informative on their own.
- **Monthly** — same idea on month-over-month returns. Smooths out daily noise; values are usually slightly lower than daily.
- **Excess vs Nifty** — fund daily return *minus* Nifty 50 daily return, then correlated. **This is the key view.** It strips the market beta everyone shares and exposes manager-specific behavior. A pair at 0.95 daily but 0.20 excess means *"same market, different active bets"* — they're not redundant. A pair at 0.95 daily *and* 0.85 excess means *"the funds really do behave the same way"* — one is likely redundant.
- **Downside (Nifty<0)** — the daily correlation computed only on days when Nifty was negative. If this is much higher than the full-period daily correlation, it means *diversification fails when you need it most* (correlations spike during drawdowns).
- **Rolling pair** — a chosen pair's correlation over a moving window (default 90d). Look for regime shifts: e.g. 0.6 in calm markets but 0.95 in 2020-style crashes.

**Reading clusters**: row/column order is held constant across the four heatmaps using hierarchical clustering on the *excess-return* matrix, so similarly-behaving funds form contiguous blocks along the diagonal — and a fund's row stays in the same position across tabs for easy comparison.
            """
        )

    t_daily, t_monthly, t_excess, t_down, t_rolling = st.tabs(
        ["Daily", "Monthly", "Excess vs Nifty", "Downside (Nifty<0)", "Rolling pair"]
    )

    with t_daily:
        st.plotly_chart(
            plot_clustered_heatmap(corr_daily, ordered, "Daily return correlation"),
            use_container_width=True,
            key="corr-daily",
        )

    with t_monthly:
        st.plotly_chart(
            plot_clustered_heatmap(corr_monthly, ordered, "Monthly return correlation"),
            use_container_width=True,
            key="corr-monthly",
        )

    with t_excess:
        if not has_nifty:
            st.warning("Nifty 50 data unavailable for this date range.")
        elif corr_excess.empty:
            st.info("Not enough overlapping data with Nifty 50.")
        else:
            st.caption("Fund daily return minus Nifty 50 daily return — strips broad-market beta.")
            st.plotly_chart(
                plot_clustered_heatmap(corr_excess, ordered, "Excess-return correlation"),
                use_container_width=True,
                key="corr-excess",
            )

    with t_down:
        if not has_nifty:
            st.warning("Nifty 50 data unavailable for this date range.")
        elif corr_down.empty:
            st.info("Not enough Nifty-down days for a meaningful correlation.")
        else:
            down_days = int((nifty.loc[nifty.index.intersection(daily_dt.index)] < 0).sum())
            total_days = len(nifty.index.intersection(daily_dt.index))
            st.caption(f"Computed on {down_days} down days out of {total_days} total Nifty trading days.")
            st.plotly_chart(
                plot_clustered_heatmap(corr_down, ordered, "Downside correlation (Nifty<0)"),
                use_container_width=True,
                key="corr-down",
            )

    with t_rolling:
        funds = list(daily.columns)
        default_pair = top_pair(corr_daily) or (funds[0], funds[1])
        c1, c2 = st.columns(2)
        a = c1.selectbox("Fund A", funds, index=funds.index(default_pair[0]), key="rolling-a")
        b_options = [f for f in funds if f != a]
        default_b = default_pair[1] if default_pair[1] in b_options else b_options[0]
        b = c2.selectbox("Fund B", b_options, index=b_options.index(default_b), key="rolling-b")
        window = st.radio("Window", [60, 90, 180, 252], index=1, horizontal=True, key="rolling-window")

        series = rolling_pair_corr(daily, a, b, window)
        if series.empty:
            st.info(f"Not enough overlapping data for a {window}-day rolling correlation.")
        else:
            st.plotly_chart(
                plot_rolling_corr(series, a, b, window),
                use_container_width=True,
                key="rolling-chart",
            )
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Mean", f"{series.mean():.2f}")
            m2.metric("Min", f"{series.min():.2f}")
            m3.metric("Max", f"{series.max():.2f}")
            m4.metric("Current", f"{series.iloc[-1]:.2f}")
