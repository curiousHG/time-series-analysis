"""Overview — sector & index performance board: what's leading / lagging over a trailing window."""

from __future__ import annotations

import plotly.express as px
import streamlit as st

from ui.charts import theme
from ui.state.loaders import load_index_performance_cached

_WINDOWS = ["1D", "1W", "1M", "3M"]


def render() -> None:
    df = load_index_performance_cached()
    if df.is_empty():
        st.caption("Index performance unavailable — open Stock Analysis once to sync index data.")
        return

    st.subheader("Sector & index performance")
    window = st.segmented_control("Window", options=_WINDOWS, default="1M", key="sector_window") or "1M"

    pdf = df.select(["label", "group", window]).drop_nulls(window).to_pandas()
    if pdf.empty:
        st.caption(f"Not enough history for a {window} view yet.")
        return
    pdf["pct"] = pdf[window] * 100
    pdf = pdf.sort_values("pct")

    fig = px.bar(
        pdf,
        x="pct",
        y="label",
        orientation="h",
        color="pct",
        color_continuous_scale="RdYlGn",
        color_continuous_midpoint=0,
        text=pdf["pct"].map(lambda v: f"{v:+.1f}%"),
    )
    fig.update_traces(textposition="outside", cliponaxis=False)
    fig.update_layout(
        height=max(320, 26 * len(pdf)),
        showlegend=False,
        coloraxis_showscale=False,
        xaxis_title=f"{window} return %",
        yaxis_title=None,
        margin=theme.COMPACT_MARGIN,
    )
    st.plotly_chart(fig, use_container_width=True, key="sector-perf-bar")
    st.caption(f"Trailing **{window}** return per index — green leads, red lags. Broad + sectoral.")
