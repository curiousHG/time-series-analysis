"""Alpha categorisation scatter — stocks on the CAPM alpha (Y) vs beta (X) plane.

Quadrants split at alpha=0 and beta=1, colour-coded by category. The market-cap-sized
bubbles let outperformers/laggards and defensive/aggressive names be read at a glance —
the stock analogue of the MF screener's alpha/beta mode.
"""

from __future__ import annotations

import math

import numpy as np
import polars as pl
import streamlit as st

from stocks.metric_catalog import CATEGORY_COLORS
from ui.charts.risk_return_scatter import render_scatter


def render_alpha_chart(df: pl.DataFrame) -> None:
    rated = df.filter(pl.col("alpha_1y").is_not_null() & pl.col("beta_1y").is_not_null())
    if rated.is_empty():
        st.info("No alpha/beta computed yet for the current selection.")
        return

    pdf = rated.to_pandas()
    caps = pdf["market_cap"].fillna(0.0)
    if caps.max() > 0:
        pdf["__size__"] = 8 + 26 * (np.sqrt(caps) / max(math.sqrt(caps.max()), 1.0))
    else:
        pdf["__size__"] = 12.0

    render_scatter(
        pdf,
        x_col="beta_1y",
        y_col="alpha_1y",
        x_title="Beta (CAPM vs Nifty 50)",
        y_title="Alpha % (annualised)",
        key="stock_alpha_scatter",
        size_col="__size__",
        color_col="alpha_category",
        color_discrete_map=dict(CATEGORY_COLORS),
        color_label="Category",
        hover_name_col="symbol",
        hover_cols=("stock_name", "return_1y", "stock_pe", "roe"),
        hline=0.0,
        vline=1.0,
        height=520,
    )
