"""Growth tab — portfolio value vs Nifty vs FD, invested over time."""

import streamlit as st
import polars as pl
import pandas as pd
import plotly.graph_objects as go

from data.store.stock import ensure_stock_data
from ui.views.portfolio_tabs.helpers import get_signed_invested


def render(mapped: pl.DataFrame, pv: pd.DataFrame):
    _render_invested_over_time(mapped)
    st.divider()
    _render_growth_comparison(mapped, pv)


def _render_invested_over_time(mapped: pl.DataFrame):
    st.subheader("Total Invested Over Time")
    invested_df = (
        mapped.with_columns(
            (pl.col("price") * pl.col("signed_qty")).alias("invested_amount")
        )
        .group_by("trade_date")
        .agg(pl.sum("invested_amount").alias("total_invested"))
        .sort("trade_date")
        .with_columns(pl.col("total_invested").cum_sum().alias("cumulative_invested"))
    )
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=invested_df["trade_date"], y=invested_df["cumulative_invested"],
        mode="lines+markers", name="Cumulative Invested",
    ))
    fig.add_trace(go.Bar(
        x=invested_df["trade_date"], y=invested_df["total_invested"],
        name="Daily Invested", opacity=0.4,
    ))
    fig.update_layout(height=400, yaxis_title="Amount", xaxis_title="Date", barmode="overlay")
    st.plotly_chart(fig, use_container_width=True, key="invested-over-time")


def _render_growth_comparison(mapped: pl.DataFrame, pv: pd.DataFrame):
    st.subheader("Portfolio Value vs Nifty 50 vs FD")

    start_dt = pv["date"].min()
    end_dt = pv["date"].max()
    signed_trades = get_signed_invested(mapped)

    # Cumulative invested
    cum_invested = (
        mapped.with_columns(
            pl.when(pl.col("signed_qty") > 0)
            .then(pl.col("trade_value"))
            .otherwise(-pl.col("trade_value"))
            .alias("signed_invested")
        )
        .group_by("trade_date")
        .agg(pl.sum("signed_invested").alias("invested"))
        .sort("trade_date")
        .with_columns(pl.col("invested").cum_sum().alias("cum_invested"))
        .rename({"trade_date": "date"})
    )
    ci = cum_invested.select(["date", "cum_invested"]).to_pandas()
    merged = pd.merge_asof(
        pv.sort_values("date"), ci.sort_values("date"), on="date", direction="backward"
    )
    merged["cum_invested"] = merged["cum_invested"].ffill().fillna(0)

    # Nifty
    nifty = ensure_stock_data("^NSEI", start_dt, end_dt)
    nifty_pd = (
        nifty.select(["Date", "Close"]).to_pandas().dropna(subset=["Close"])
        .rename(columns={"Date": "date", "Close": "nifty_close"})
    )

    if len(nifty_pd) > 0:
        tn = pd.merge_asof(
            signed_trades.sort_values("date"),
            nifty_pd[["date", "nifty_close"]].sort_values("date"),
            on="date", direction="nearest",
        )
        tn["nifty_units"] = tn["signed_invested"] / tn["nifty_close"]
        nc = tn.groupby("date")["nifty_units"].sum().cumsum().reset_index()
        nc.columns = ["date", "cum_nifty_units"]
        merged = pd.merge_asof(
            merged.sort_values("date"), nc.sort_values("date"), on="date", direction="backward"
        )
        merged["cum_nifty_units"] = merged["cum_nifty_units"].ffill().fillna(0)
        merged = pd.merge_asof(
            merged.sort_values("date"),
            nifty_pd[["date", "nifty_close"]].sort_values("date"),
            on="date", direction="backward",
        )
        merged["nifty_value"] = merged["cum_nifty_units"] * merged["nifty_close"]

    # FD
    interest_rate = 0.065
    fd_values = []
    for d in merged["date"].values:
        total = sum(
            t["signed_invested"]
            * (1 + interest_rate) ** (max(0, (pd.Timestamp(d) - pd.Timestamp(t["date"])).days) / 365.25)
            for _, t in signed_trades.iterrows()
            if pd.Timestamp(d) >= pd.Timestamp(t["date"])
        )
        fd_values.append(total)
    merged["fd_value"] = fd_values

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=merged["date"], y=merged["portfolio_value"], mode="lines", name="Portfolio", line=dict(color="#6366f1", width=2)))
    fig.add_trace(go.Scatter(x=merged["date"], y=merged["cum_invested"], mode="lines", name="Invested", line=dict(color="#94a3b8", width=1, dash="dot")))
    if "nifty_value" in merged.columns:
        fig.add_trace(go.Scatter(x=merged["date"], y=merged["nifty_value"], mode="lines", name="If Nifty 50", line=dict(color="#f59e0b", width=2)))
    fig.add_trace(go.Scatter(x=merged["date"], y=merged["fd_value"], mode="lines", name=f"If {interest_rate*100:.1f}% FD", line=dict(color="#10b981", width=1, dash="dash")))
    fig.update_layout(height=450, yaxis_title="Value (INR)", xaxis_title="Date", hovermode="x unified", legend=dict(orientation="h", y=1.1))
    st.plotly_chart(fig, use_container_width=True, key="portfolio-growth")
