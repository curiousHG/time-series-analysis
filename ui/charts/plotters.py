import numpy as np
from scipy.stats import gaussian_kde
import plotly.graph_objects as go
import plotly.express as px
import polars as pl
import pandas as pd


from mutual_funds.analytics import sector_exposure


def plot_sector_stack(sector_df: pl.DataFrame, fund_slugs: list[str]):
    # sort by weight descending
    df = sector_exposure(sector_df, fund_slugs).sort(by='weight', descending=True).to_pandas()
    # change bars to log scale
    # df['weight'] = np.log10(df['weight'])

    return px.bar(
        df,
        x="weight",
        y="sector",
        color="schemeSlug",
        orientation="h",
        title="Sector Exposure Comparison",
    )


def plot_overlap_heatmap(matrix: pd.DataFrame):
    fig = px.imshow(
        matrix,
        text_auto=".1f",
        color_continuous_scale="Reds",
        aspect="auto",
        title="Fund Overlap (%)",
    )
    return fig.update_layout(xaxis_title="Fund", yaxis_title="Fund")


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
                hovertemplate="Return: %{x:.2%}<br>Density: %{y:.2f}<extra></extra>",
            )
        )

    fig.update_layout(
        title="Daily Return Distribution (KDE)",
        xaxis_title="Daily Return",
        yaxis_title="Density",
        hovermode="x unified",
    )

    fig.update_xaxes(tickformat=".1%")
    return fig
