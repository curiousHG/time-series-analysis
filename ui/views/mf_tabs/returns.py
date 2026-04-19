"""Returns & Distributions tab."""

import streamlit as st
import polars as pl
import pandas as pd

from ui.charts.plotters import plot_kde_returns
from ui.components.mutual_funds_rolling_returns import show_rolling_returns_info


def render(nav_pd: pd.DataFrame, selected_registry: pl.DataFrame, nav_df: pl.DataFrame):
    if nav_pd.empty:
        st.info("No NAV data available.")
        return

    pct = (
        nav_pd.pivot(index="date", columns="schemeName", values="nav")
        .pct_change()
        .dropna()
    )
    st.plotly_chart(plot_kde_returns(pct), use_container_width=True, key="kde-returns")
    show_rolling_returns_info(selected_registry, nav_df)
