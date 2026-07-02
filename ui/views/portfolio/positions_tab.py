"""Portfolio · Positions tab — look-through exposure, overlap, holdings deep-dive."""

from __future__ import annotations

import polars as pl
import streamlit as st

from mutual_funds.display import short_scheme_name
from services.portfolio_analytics import fund_values_from_nav, lookthrough_exposure
from ui.views.mutual_fund import holdings, overlap

TOP_N_EXPOSURE = 25


def render(
    mapped: pl.DataFrame,
    portfolio_nav: pl.DataFrame,
    holdings_df: pl.DataFrame,
    sectors_df: pl.DataFrame,
    assets_df: pl.DataFrame,
    active_registry: pl.DataFrame,
) -> None:
    _render_lookthrough(mapped, portfolio_nav, holdings_df)
    st.divider()
    with st.expander("Sector overlap across funds", expanded=False):
        overlap.render(holdings_df, sectors_df, active_registry)
    with st.expander("Per-fund holdings deep-dive", expanded=False):
        holdings.render(holdings_df, sectors_df, assets_df, active_registry)


def _render_lookthrough(mapped: pl.DataFrame, portfolio_nav: pl.DataFrame, holdings_df: pl.DataFrame) -> None:
    st.subheader("Look-through exposure — what you actually own")
    fund_values = fund_values_from_nav(mapped, portfolio_nav)
    exposure = lookthrough_exposure(holdings_df, fund_values)
    if exposure.is_empty():
        st.info("No holdings data for the active funds yet — refresh holdings from Settings.")
        return

    covered = float(exposure["exposure_pct"].sum())
    st.caption(
        f"Each fund's latest holdings weighted by its share of your portfolio "
        f"({covered:.0f}% of portfolio value mapped). Top {TOP_N_EXPOSURE} instruments shown."
    )
    display = (
        exposure.head(TOP_N_EXPOSURE)
        .with_columns(
            pl.col("funds")
            .list.eval(pl.element().map_elements(short_scheme_name, return_dtype=pl.Utf8))
            .list.join(", ")
            .alias("via")
        )
        .select(
            pl.col("instrument_name").alias("Instrument"),
            pl.col("industry").alias("Industry"),
            pl.col("market_cap").alias("Cap"),
            pl.col("exposure_pct").alias("Portfolio %"),
            pl.col("n_funds").alias("# Funds"),
            pl.col("via").alias("Held via"),
        )
        .to_pandas()
    )
    st.dataframe(
        display,
        use_container_width=True,
        hide_index=True,
        column_config={"Portfolio %": st.column_config.NumberColumn(format="%.2f%%")},
    )
