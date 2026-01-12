import streamlit as st
import polars as pl
import pandas as pd

from data.store.mutualfund import (
    ensure_holdings_data,
    ensure_nav_data,
    save_to_registry,
)
from ui.components.fund_picker import fund_picker
from data.store.mutualfund import (
    load_registry,
)
from mutual_funds.analytics import overlap_matrix, sector_exposure

from ui.charts.correlation_heatmap import render_correlation_heatmap
from ui.components.mutual_fund_holdings import render_holdings_table
from ui.components.mutual_funds_rolling_returns import show_rolling_returns_info
from mutual_funds.tradebook import (
    compute_current_holdings,
)
from ui.components.fund_matcher import fund_matcher
from ui.data.loaders import load_nav_and_holdings, load_txn_data
from ui.utils import get_selected_registry
from ui.charts.plotters import plot_kde_returns, plot_overlap_heatmap, plot_sector_stack


st.title("💼 Mutual Funds")

selected_schemes = fund_picker(
    load_registry=load_registry,
    save_to_registry=save_to_registry,
)

selected_registry = get_selected_registry(load_registry)
st.data_editor(selected_registry)
selected_scheme_names = selected_registry["schemeName"].to_list()
selected_scheme_slugs = selected_registry["schemeSlug"].to_list()


# ---- cached data loads
txn_df = load_txn_data("data/user/tradebook-MF.csv")

nav_df, holdings_df, sectors_df, assets_df = load_nav_and_holdings(
    selected_scheme_names,
    selected_scheme_slugs,
)

nav_df = nav_df.join(selected_registry, on="schemeName", how="inner")
nav_pd = nav_df.to_pandas()


tab_map, tab_portfolio, tab_overlap, tab_returns, tab_holdings, tab_correlation = (
    st.tabs(
        [
            "🔗 Mapping",
            "📊 Portfolio",
            "🧩 Overlap & Allocation",
            "📈 Returns",
            "📋 Holdings",
            "🔥 Correlation",
        ]
    )
)


with tab_map:
    if not txn_df:
        st.write("Add the transaction dataframe")
    else:
        fund_matcher(txn_df)

with tab_portfolio:
    st.header("Current Portfolio")

    st.subheader("Price & Trades")
    # st.plotly_chart(fig, width="stretch")
    if not txn_df:
        st.write("Add the transaction dataframe")
    else:
        current_holdings = compute_current_holdings(txn_df)
        st.dataframe(current_holdings)

with tab_overlap:
    st.header("Overlap & Allocation")
    matrix = overlap_matrix(holdings_df, selected_scheme_slugs)

    fig = plot_overlap_heatmap(matrix)
    st.plotly_chart(fig, width="stretch")
    if not sectors_df.height:
        st.write("No sector data")
    else:
        fig = plot_sector_stack(sectors_df, selected_scheme_slugs)
        st.plotly_chart(fig, width="stretch")

with tab_returns:
    st.header("Returns & Distributions")
    pct = (
        nav_pd.pivot(index="date", columns="schemeName", values="nav")
        .pct_change()
        .dropna()
    )

    fig = plot_kde_returns(pct)
    st.plotly_chart(fig, width="stretch")

    show_rolling_returns_info(selected_registry, nav_df)

with tab_holdings:
    st.header("Fund Holdings")

    for scheme in selected_scheme_slugs:
        render_holdings_table(holdings_df, sectors_df, assets_df, scheme)

with tab_correlation:
    st.header("Correlation")

    corr = (
        nav_pd.pivot(index="date", columns="schemeName", values="nav")
        .pct_change(fill_method=None)
        .dropna()
        .corr()
    )

    fig = render_correlation_heatmap(corr)
    st.plotly_chart(fig, width="stretch")
