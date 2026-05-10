"""Holdings tab — treemap + donut charts per fund."""

import polars as pl
import streamlit as st

from ui.components.mutual_fund_holdings import render_holdings_table


def render(
    holdings_df: pl.DataFrame,
    sectors_df: pl.DataFrame,
    assets_df: pl.DataFrame,
    selected_registry: pl.DataFrame,
):
    if not holdings_df.height:
        st.info("No holdings data. Fetch it from the Data Manager page.")
        return

    slug_to_short = (
        dict(zip(selected_registry["schemeSlug"].to_list(), selected_registry["shortName"].to_list(), strict=False))
        if "shortName" in selected_registry.columns
        else {}
    )
    for slug in selected_registry["schemeSlug"].to_list():
        render_holdings_table(holdings_df, sectors_df, assets_df, slug, display_name=slug_to_short.get(slug, slug))
