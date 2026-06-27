"""Alpha categorisation scatter — stocks on the CAPM alpha (Y) vs beta (X) plane.

Quadrants split at alpha=0 and beta=1, colour-coded by category. The market-cap-sized
bubbles let outperformers/laggards and defensive/aggressive names be read at a glance —
the stock analogue of the MF screener's alpha/beta mode.
"""

from __future__ import annotations

import math

import numpy as np
import plotly.graph_objects as go
import polars as pl
import streamlit as st

from stocks.metric_catalog import CATEGORY_COLORS


def render_alpha_chart(df: pl.DataFrame) -> None:
    rated = df.filter(pl.col("alpha_1y").is_not_null() & pl.col("beta_1y").is_not_null())
    if rated.is_empty():
        st.info("No alpha/beta computed yet for the current selection.")
        return

    pdf = rated.to_pandas()
    caps = pdf["market_cap"].fillna(0.0)
    sizes = 8 + 26 * (np.sqrt(caps) / max(math.sqrt(caps.max()), 1.0)) if caps.max() > 0 else 12

    fig = go.Figure()
    for cat, color in CATEGORY_COLORS.items():
        sub = pdf[pdf["alpha_category"] == cat]
        if sub.empty:
            continue
        sub_sizes = sizes[sub.index] if hasattr(sizes, "__getitem__") and not isinstance(sizes, int) else 12
        fig.add_trace(
            go.Scatter(
                x=sub["beta_1y"],
                y=sub["alpha_1y"],
                mode="markers",
                name=cat,
                marker={"color": color, "size": sub_sizes, "line": {"width": 0.5, "color": "#0f1117"}, "opacity": 0.85},
                customdata=sub[["stock_name", "return_1y", "stock_pe", "roe"]],
                text=sub["symbol"],
                hovertemplate=(
                    "<b>%{text}</b> — %{customdata[0]}<br>"
                    "Alpha: %{y:.1f}%  ·  Beta: %{x:.2f}<br>"
                    "1Y Return: %{customdata[1]:.1f}%  ·  P/E: %{customdata[2]:.1f}  ·  ROE: %{customdata[3]:.1f}%"
                    "<extra></extra>"
                ),
            )
        )

    fig.add_hline(y=0, line_dash="dash", line_color="#475569")
    fig.add_vline(x=1, line_dash="dash", line_color="#475569")
    fig.update_layout(
        height=520,
        margin={"l": 50, "r": 20, "t": 20, "b": 40},
        paper_bgcolor="#0f1117",
        plot_bgcolor="#0f1117",
        font={"color": "#e2e8f0"},
        xaxis={"title": "Beta (CAPM vs Nifty 50)", "gridcolor": "#1e293b", "zeroline": False},
        yaxis={"title": "Alpha % (annualised)", "gridcolor": "#1e293b", "zeroline": False},
        legend={"orientation": "h", "y": 1.02, "yanchor": "bottom"},
    )
    st.plotly_chart(fig, use_container_width=True, key="stock_alpha_scatter")
