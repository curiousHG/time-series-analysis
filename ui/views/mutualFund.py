import streamlit as st
import polars as pl
from plotly import graph_objects as go

from data.store.mutualfund import (
    load_registry,
    save_to_registry,
)
from ui.components.fund_picker import fund_picker
from mutual_funds.analytics import overlap_matrix
from ui.charts.correlation_heatmap import render_correlation_heatmap
from ui.components.mutual_fund_holdings import render_holdings_table
from ui.components.mutual_funds_rolling_returns import show_rolling_returns_info
from mutual_funds.tradebook import compute_current_holdings
from ui.components.fund_matcher import fund_matcher
from ui.state.loaders import load_holdings_data, load_nav_data, load_txn_data
from ui.utils import get_selected_registry
from ui.charts.plotters import plot_kde_returns, plot_overlap_heatmap, plot_sector_stack


st.title("Mutual Funds")

fund_picker(load_registry=load_registry, save_to_registry=save_to_registry)

selected_registry = get_selected_registry(load_registry)
selected_scheme_names = selected_registry["schemeName"].to_list()
selected_scheme_slugs = selected_registry["schemeSlug"].to_list()

# ---- cached data loads
txn_df = load_txn_data()
nav_df = load_nav_data(selected_scheme_names)
holdings_df, sectors_df, assets_df = load_holdings_data(selected_scheme_slugs)

nav_df = nav_df.join(selected_registry, on="schemeName", how="inner")
nav_pd = nav_df.to_pandas()

# ---- tabs
tab_mapping, tab_portfolio, tab_overlap, tab_returns, tab_holdings, tab_corr = st.tabs(
    ["Mapping", "Portfolio", "Overlap & Allocation", "Returns", "Holdings", "Correlation"]
)

with tab_mapping:
    if txn_df is None:
        st.info("No tradebook data. Upload a CSV from the Data Manager page.")
    else:
        fund_matcher(txn_df)

with tab_portfolio:
    if txn_df is None:
        st.info("Upload a tradebook CSV to see portfolio data.")
    else:
        st.subheader("Current Holdings")
        st.dataframe(compute_current_holdings(txn_df))

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
        fig.add_trace(
            go.Scatter(
                x=invested_df["trade_date"],
                y=invested_df["cumulative_invested"],
                mode="lines+markers",
                name="Cumulative Invested",
            )
        )
        fig.add_trace(
            go.Bar(
                x=invested_df["trade_date"],
                y=invested_df["total_invested"],
                name="Daily Invested",
                opacity=0.4,
            )
        )
        fig.update_layout(
            height=400,
            yaxis_title="Amount",
            xaxis_title="Date",
            barmode="overlay",
        )
        st.plotly_chart(fig, use_container_width=True)

with tab_overlap:
    if not holdings_df.height:
        st.info("No holdings data. Fetch it from the Data Manager page.")
    else:
        matrix = overlap_matrix(holdings_df, selected_scheme_slugs)
        st.plotly_chart(plot_overlap_heatmap(matrix), use_container_width=True)

        if sectors_df.height:
            st.plotly_chart(
                plot_sector_stack(sectors_df, selected_scheme_slugs),
                use_container_width=True,
            )

with tab_returns:
    if nav_pd.empty:
        st.info("No NAV data available.")
    else:
        pct = (
            nav_pd.pivot(index="date", columns="schemeName", values="nav")
            .pct_change()
            .dropna()
        )
        st.plotly_chart(plot_kde_returns(pct), use_container_width=True)
        show_rolling_returns_info(selected_registry, nav_df)

with tab_holdings:
    if not holdings_df.height:
        st.info("No holdings data. Fetch it from the Data Manager page.")
    else:
        for scheme in selected_scheme_slugs:
            render_holdings_table(holdings_df, sectors_df, assets_df, scheme)

with tab_corr:
    if nav_pd.empty:
        st.info("No NAV data available.")
    else:
        corr = (
            nav_pd.pivot(index="date", columns="schemeName", values="nav")
            .pct_change(fill_method=None)
            .corr(min_periods=30)
            .fillna(0)
        )
        st.plotly_chart(render_correlation_heatmap(corr), use_container_width=True)
