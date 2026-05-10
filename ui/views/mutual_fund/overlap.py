"""Overlap & Allocation tab."""

import polars as pl
import streamlit as st

from mutual_funds.analytics import overlap_matrix
from ui.charts.plotters import plot_overlap_heatmap, plot_sector_average, plot_sector_stack


def render(holdings_df: pl.DataFrame, sectors_df: pl.DataFrame, selected_registry: pl.DataFrame):
    if not holdings_df.height:
        st.info("No holdings data. Fetch it from the Data Manager page.")
        return

    slugs = selected_registry["schemeSlug"].to_list()
    slug_to_short = (
        dict(zip(selected_registry["schemeSlug"].to_list(), selected_registry["shortName"].to_list(), strict=False))
        if "shortName" in selected_registry.columns
        else None
    )

    matrix = overlap_matrix(holdings_df, slugs)
    st.plotly_chart(
        plot_overlap_heatmap(matrix, slug_to_short),
        use_container_width=True,
        key="overlap-heatmap",
    )

    if sectors_df.height:
        st.plotly_chart(
            plot_sector_average(sectors_df, slugs),
            use_container_width=True,
            key="sector-average",
        )
        with st.expander("Per-fund breakdown"):
            st.plotly_chart(
                plot_sector_stack(sectors_df, slugs, slug_to_short),
                use_container_width=True,
                key="sector-stack",
            )
