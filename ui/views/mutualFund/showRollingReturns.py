import streamlit as st
import polars as pl

from src.mutualFunds.analytics import rolling_return_summary, rolling_returns
from ui.charts.indicator_chart import render_indicator

def show_rolling_returns_info(selected_registry: pl.DataFrame, nav_df:pl.DataFrame):
    
    nav_df = nav_df.join(selected_registry, on="schemeName", how="inner")
    nav_pd = nav_df.to_pandas()
    st.subheader("Analytics")
    ROLLING_WINDOWS = {
        "3 Months": 63,
        "6 Months": 126,
        "1 Year": 252,
        "3 Years": 756,
    }

    window_label = st.selectbox(
        "Rolling Return Window",
        options=list(ROLLING_WINDOWS.keys()),
    )

    rolling_window = ROLLING_WINDOWS[window_label]
    if nav_pd.empty:
        st.warning("No NAV data available")
        return

    # ---- Compute rolling returns
    rr = rolling_returns(nav_pd, rolling_window)

    if rr.empty:
        st.warning("Not enough data for rolling returns")
        return

    st.subheader("📋 Rolling Return Summary")

    rr_summary = rolling_return_summary(rr)

    st.dataframe(
        rr_summary,
        use_container_width=True,
    )
    st.subheader("📈 Rolling Return Charts")

    cols = st.columns(3)
    col_idx = 0

    for scheme_name in rr.columns:
        series = rr[scheme_name].dropna()
        if series.empty:
            continue

        with cols[col_idx]:
            render_indicator(
                f"{scheme_name} ({window_label})",
                series,
            )

        col_idx = (col_idx + 1) % 3

