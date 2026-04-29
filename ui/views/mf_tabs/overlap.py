"""Overlap & Allocation tab."""

import polars as pl
import streamlit as st

from mutual_funds.analytics import overlap_matrix
from ui.charts.plotters import plot_overlap_heatmap, plot_sector_stack


def render(holdings_df: pl.DataFrame, sectors_df: pl.DataFrame, slugs: list[str]):
    if not holdings_df.height:
        st.info("No holdings data. Fetch it from the Data Manager page.")
        return

    matrix = overlap_matrix(holdings_df, slugs)
    st.plotly_chart(plot_overlap_heatmap(matrix), use_container_width=True, key="overlap-heatmap")

    if sectors_df.height:
        st.plotly_chart(
            plot_sector_stack(sectors_df, slugs),
            use_container_width=True,
            key="sector-stack",
        )
