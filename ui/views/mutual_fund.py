"""Mutual Fund Analysis — main page with tab routing."""

import streamlit as st

from data.store.mutual_fund import load_registry, save_to_registry
from ui.components.fund_picker import fund_picker
from ui.state.loaders import load_holdings_data, load_nav_data, load_txn_data
from ui.utils import get_selected_registry
from ui.views.mf_tabs import portfolio, overlap, returns, holdings, correlation


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
tab_portfolio, tab_overlap, tab_returns, tab_holdings, tab_corr = st.tabs(
    ["Portfolio", "Overlap & Allocation", "Returns", "Holdings", "Correlation"]
)

with tab_portfolio:
    portfolio.render(txn_df, nav_df)

with tab_overlap:
    overlap.render(holdings_df, sectors_df, selected_scheme_slugs)

with tab_returns:
    returns.render(nav_pd, selected_registry, nav_df)

with tab_holdings:
    holdings.render(holdings_df, sectors_df, assets_df, selected_scheme_slugs)

with tab_corr:
    correlation.render(nav_pd)
