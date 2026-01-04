import streamlit as st
import polars as pl
import pandas as pd
import numpy as np
from scipy.stats import gaussian_kde
import plotly.graph_objects as go
import plotly.express as px
from src.mutualFunds.data_store import ensure_holdings_data, ensure_nav_data
from ui.components.fund_picker import fund_picker
from src.mutualFunds.registry import (
    load_registry,
    save_to_registry,
    fetch_scheme_registry,
)
from src.mutualFunds.analytics import overlap_matrix, sector_exposure

from ui.charts.correlation_heatmap import render_correlation_heatmap
from ui.views.mutualFund.showHoldingsTable import show_holdings_data
from ui.views.mutualFund.showRollingReturns import show_rolling_returns_info
from ui.views.mutualFund.utils import get_selected_registry


def plot_sector_stack(sector_df: pl.DataFrame, fund_slugs: list[str]):
    df = sector_exposure(sector_df, fund_slugs).to_pandas()

    fig = px.bar(
        df,
        x="weight",
        y="sector",
        color="schemeSlug",
        orientation="h",
        title="Sector Exposure Comparison",
    )

    st.plotly_chart(fig, use_container_width=True)


def plot_overlap_heatmap(matrix: pd.DataFrame):
    fig = px.imshow(
        matrix,
        text_auto=".1f",
        color_continuous_scale="Reds",
        aspect="auto",
        title="Fund Overlap (%)",
    )
    fig.update_layout(xaxis_title="Fund", yaxis_title="Fund")
    st.plotly_chart(fig, use_container_width=True)


def plot_kde_returns(pct: pd.DataFrame):
    fig = go.Figure()

    x_grid = np.linspace(-0.06, 0.06, 500)  # common return range

    for scheme in pct.columns:
        series = pct[scheme].dropna()

        if len(series) < 50:
            continue  # not enough data for stable KDE

        kde = gaussian_kde(series)
        y = kde(x_grid)

        fig.add_trace(
            go.Scatter(
                x=x_grid,
                y=y,
                mode="lines",
                name=scheme,
                hovertemplate="Return: %{x:.2%}<br>Density: %{y:.2f}<extra></extra>"
            )
        )

    fig.update_layout(
        title="Daily Return Distribution (KDE)",
        xaxis_title="Daily Return",
        yaxis_title="Density",
        hovermode="x unified",
    )

    fig.update_xaxes(tickformat=".1%")

    st.plotly_chart(fig, width='content')

def render_pct_change_comparison(nav_pd: pd.DataFrame):
    pct = (
        nav_pd
        .pivot(index="date", columns="schemeName", values="nav")
        .pct_change()
        .dropna()
    )

    plot_kde_returns(pct)

def show_stock_overlap(
    holdings_df: pl.DataFrame, sector_df: pl.DataFrame, selected_scheme_slugs
):
    st.write("## Stock Overlap")
    matrix = overlap_matrix(holdings_df, selected_scheme_slugs)
    plot_overlap_heatmap(matrix)
    plot_sector_stack(sector_df, selected_scheme_slugs)


def show_correlation_heatmap(selected_registry: pl.DataFrame, nav_pd: pl.DataFrame):
    st.subheader("🔥 Nav Correlation Heatmap")
    nav_df = nav_pd.join(selected_registry, on="schemeName", how="inner")
    nav_pd = nav_df.to_pandas()
    corr = (
        nav_pd.pivot(index="date", columns="schemeName", values="nav")
        .pct_change(fill_method=None)
        .dropna()
        .corr()
    )
    st.dataframe(corr)

    render_correlation_heatmap(corr)


def render():

    st.title("💼 Mutual Funds")

    fund_picker(
        load_registry=load_registry,
        save_to_registry=save_to_registry,
    )
    selected_registry = get_selected_registry(load_registry)

    st.data_editor(selected_registry)

    selected_scheme_names = selected_registry["schemeName"].to_list()
    selected_scheme_slugs = selected_registry["schemeSlug"].to_list()
    nav_df = ensure_nav_data(selected_scheme_names)
    holdings_df, sectors_df, assets_df = ensure_holdings_data(selected_scheme_slugs)
    nav_df = nav_df.join(selected_registry, on="schemeName", how="inner")
    nav_pd = nav_df.to_pandas()
    show_stock_overlap(holdings_df, sectors_df, selected_scheme_slugs)
    render_pct_change_comparison(nav_pd)
    show_holdings_data(selected_scheme_slugs, holdings_df, sectors_df, assets_df)
    show_rolling_returns_info(selected_registry, nav_df)

    show_correlation_heatmap(selected_registry, nav_df)


render()
