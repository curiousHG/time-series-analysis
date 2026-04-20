import numpy as np
import plotly.graph_objects as go
import plotly.express as px
import polars as pl
import pandas as pd

from mutual_funds.analytics import sector_exposure


def plot_sector_stack(sector_df: pl.DataFrame, fund_slugs: list[str]):
    df = (
        sector_exposure(sector_df, fund_slugs)
        .sort(by="weight", descending=True)
        .to_pandas()
    )
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


def _numpy_kde(data: np.ndarray, x_grid: np.ndarray, bandwidth: float) -> np.ndarray:
    """Simple Gaussian KDE using numpy — no scipy needed."""
    n = len(data)
    kernel = np.exp(-0.5 * ((x_grid[:, None] - data[None, :]) / bandwidth) ** 2)
    return kernel.sum(axis=1) / (n * bandwidth * np.sqrt(2 * np.pi))


def plot_kde_returns(pct: pd.DataFrame):
    fig = go.Figure()

    x_grid = np.linspace(-0.06, 0.06, 500)

    for scheme in pct.columns:
        series = pct[scheme].dropna().values

        if len(series) < 50 or np.std(series) == 0:
            continue

        # Silverman's rule of thumb for bandwidth
        bandwidth = 1.06 * np.std(series) * len(series) ** (-1 / 5)
        y = _numpy_kde(series, x_grid, bandwidth)

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
