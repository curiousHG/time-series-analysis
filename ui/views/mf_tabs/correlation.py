"""Correlation tab."""

import streamlit as st
import pandas as pd

from ui.charts.correlation_heatmap import render_correlation_heatmap


def render(nav_pd: pd.DataFrame):
    if nav_pd.empty:
        st.info("No NAV data available.")
        return

    corr = (
        nav_pd.pivot(index="date", columns="schemeName", values="nav")
        .pct_change(fill_method=None)
        .corr(min_periods=30)
        .fillna(0)
    )
    st.plotly_chart(render_correlation_heatmap(corr), use_container_width=True, key="correlation")
