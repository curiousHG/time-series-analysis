"""Portfolio tab — fund allocation, invested over time, growth comparison."""

import streamlit as st
import polars as pl
import pandas as pd
from plotly import graph_objects as go

from data.store.stock import ensure_stock_data
from data.store.mutual_fund import ensure_fund_mapping
from mutual_funds.tradebook import apply_fund_mapping, compute_daily_units


def render(txn_df: pl.DataFrame | None, nav_df: pl.DataFrame):
    if txn_df is None:
        st.info("Upload a tradebook CSV from the Data Manager page.")
        return

    fund_mapping_df = ensure_fund_mapping()
    if fund_mapping_df is None or fund_mapping_df.empty:
        st.info("No fund mappings. Sync AMFI data and upload a tradebook in the Data Manager.")
        return

    mapped = apply_fund_mapping(txn_df, fund_mapping_df)
    mapped = mapped.filter(pl.col("schemeName").is_not_null() & (pl.col("schemeName") != ""))

    if mapped.is_empty():
        st.info("No mapped funds found.")
        return

    _render_allocation_table(mapped, nav_df)
    st.divider()
    _render_invested_over_time(txn_df)
    st.divider()
    _render_growth_comparison(mapped, nav_df)


def _render_allocation_table(mapped: pl.DataFrame, nav_df: pl.DataFrame):
    st.subheader("Fund Allocation")

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
    alloc_df["Allocation %"] = (alloc_df["Current Value"] / total_value * 100).round(1)
    alloc_df = alloc_df.sort_values("Current Value", ascending=False)

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

    total_invested = alloc_df["Invested"].sum()
    total_pnl = alloc_df["P&L"].sum()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Invested", f"{total_invested:,.0f}")
    c2.metric("Current Value", f"{total_value:,.0f}")
    c3.metric("P&L", f"{total_pnl:,.0f}")
    c4.metric("P&L %", f"{total_pnl / total_invested * 100:.2f}%")


def _render_invested_over_time(txn_df: pl.DataFrame):
    st.subheader("Total Invested Over Time")
    invested_df = (
        txn_df.with_columns(
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


def _render_growth_comparison(mapped: pl.DataFrame, nav_df: pl.DataFrame):
    st.subheader("Portfolio Value vs Nifty 50 vs FD")

    daily_units = compute_daily_units(mapped)
    portfolio_nav = nav_df.select(["date", "nav", "schemeName"])
    all_dates = portfolio_nav.select("date").unique().sort("date")

    unit_frames = []
    for scheme in daily_units.select("schemeName").unique().to_series().to_list():
        su = daily_units.filter(pl.col("schemeName") == scheme)
        sf = (
            all_dates.join(su, on="date", how="left")
            .with_columns(pl.lit(scheme).alias("schemeName"))
            .sort("date")
            .with_columns(pl.col("units").forward_fill().fill_null(0))
        )
        unit_frames.append(sf)

    if not unit_frames:
        return

    all_units = pl.concat(unit_frames)
    portfolio_value = (
        all_units.join(portfolio_nav, on=["date", "schemeName"], how="inner")
        .with_columns((pl.col("units") * pl.col("nav")).alias("value"))
        .group_by("date")
        .agg(pl.sum("value").alias("portfolio_value"))
        .sort("date")
        .filter(pl.col("portfolio_value") > 0)
    )

    if portfolio_value.height == 0:
        st.info("Not enough data to compute portfolio growth.")
        return

    start_dt = portfolio_value["date"].min()
    end_dt = portfolio_value["date"].max()

    # Cumulative invested
    cum_invested = (
        mapped.group_by("trade_date")
        .agg(pl.sum("trade_value").alias("invested"))
        .sort("trade_date")
        .with_columns(pl.col("invested").cum_sum().alias("cum_invested"))
        .rename({"trade_date": "date"})
    )

    pv = portfolio_value.to_pandas()
    ci = cum_invested.select(["date", "cum_invested"]).to_pandas()
    merged = pd.merge_asof(pv.sort_values("date"), ci.sort_values("date"), on="date", direction="backward")
    merged["cum_invested"] = merged["cum_invested"].ffill().fillna(0)

    # Nifty comparison
    nifty = ensure_stock_data("^NSEI", start_dt, end_dt)
    nifty_pd = nifty.select(["Date", "Close"]).to_pandas().dropna(subset=["Close"])
    nifty_pd = nifty_pd.rename(columns={"Date": "date", "Close": "nifty_close"})

    if len(nifty_pd) > 0:
        trades = mapped.select(["trade_date", "trade_value"]).to_pandas().rename(columns={"trade_date": "date"})
        trades = pd.merge_asof(trades.sort_values("date"), nifty_pd[["date", "nifty_close"]].sort_values("date"), on="date", direction="nearest")
        trades["nifty_units"] = trades["trade_value"] / trades["nifty_close"]
        nifty_cum = trades.groupby("date")["nifty_units"].sum().cumsum().reset_index()
        nifty_cum.columns = ["date", "cum_nifty_units"]
        merged = pd.merge_asof(merged.sort_values("date"), nifty_cum.sort_values("date"), on="date", direction="backward")
        merged["cum_nifty_units"] = merged["cum_nifty_units"].ffill().fillna(0)
        merged = pd.merge_asof(merged.sort_values("date"), nifty_pd[["date", "nifty_close"]].sort_values("date"), on="date", direction="backward")
        merged["nifty_value"] = merged["cum_nifty_units"] * merged["nifty_close"]

    # FD comparison
    fd_trades = mapped.select(["trade_date", "trade_value"]).to_pandas()
    interest_rate = 0.065
    fd_values = []
    for d in merged["date"].values:
        total = sum(
            t["trade_value"] * (1 + interest_rate) ** (max(0, (pd.Timestamp(d) - pd.Timestamp(t["trade_date"])).days) / 365.25)
            for _, t in fd_trades.iterrows()
            if pd.Timestamp(d) >= pd.Timestamp(t["trade_date"])
        )
        fd_values.append(total)
    merged["fd_value"] = fd_values

    # Plot
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=merged["date"], y=merged["portfolio_value"], mode="lines", name="Portfolio", line=dict(color="#6366f1", width=2)))
    fig.add_trace(go.Scatter(x=merged["date"], y=merged["cum_invested"], mode="lines", name="Invested", line=dict(color="#94a3b8", width=1, dash="dot")))
    if "nifty_value" in merged.columns:
        fig.add_trace(go.Scatter(x=merged["date"], y=merged["nifty_value"], mode="lines", name="If Nifty 50", line=dict(color="#f59e0b", width=2)))
    fig.add_trace(go.Scatter(x=merged["date"], y=merged["fd_value"], mode="lines", name="If 6.5% FD", line=dict(color="#10b981", width=1, dash="dash")))
    fig.update_layout(height=450, yaxis_title="Value (INR)", xaxis_title="Date", hovermode="x unified", legend=dict(orientation="h", y=1.1))
    st.plotly_chart(fig, use_container_width=True, key="portfolio-growth")
