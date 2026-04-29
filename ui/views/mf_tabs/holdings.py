"""Holdings tab — treemap + donut charts per fund."""

import polars as pl
import streamlit as st

from ui.components.mutual_fund_holdings import render_holdings_table


def render(
    holdings_df: pl.DataFrame,
    sectors_df: pl.DataFrame,
    assets_df: pl.DataFrame,
    slugs: list[str],
):
    if not holdings_df.height:
        st.info("No holdings data. Fetch it from the Data Manager page.")
        return

    for scheme in slugs:
        render_holdings_table(holdings_df, sectors_df, assets_df, scheme)
