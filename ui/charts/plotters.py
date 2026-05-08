import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import polars as pl

from mutual_funds.analytics import sector_exposure, sector_exposure_average


def plot_sector_stack(sector_df: pl.DataFrame, fund_slugs: list[str], slug_to_short: dict[str, str] | None = None):
    df = sector_exposure(sector_df, fund_slugs).sort(by="weight", descending=True).to_pandas()
    if slug_to_short:
        df["fund"] = df["schemeSlug"].map(lambda s: slug_to_short.get(s, s))
        color_col = "fund"
    else:
        color_col = "schemeSlug"
    fig = px.bar(
        df,
        x="weight",
        y="sector",
        color=color_col,
        orientation="h",
        barmode="group",
        title="Sector Exposure by Fund",
    )
    return fig.update_layout(xaxis_title="Weight (% of fund)")


def plot_sector_average(sector_df: pl.DataFrame, fund_slugs: list[str]):
    df = sector_exposure_average(sector_df, fund_slugs).to_pandas()
    fig = px.pie(
        df,
        values="avg_weight",
        names="sector",
        title=f"Average Sector Exposure (across {len(fund_slugs)} selected funds)",
        hover_data=["fund_count"],
    )
    return fig.update_traces(textposition="inside", textinfo="percent+label")


def plot_overlap_heatmap(matrix: pd.DataFrame, slug_to_short: dict[str, str] | None = None):
    if slug_to_short:
        rename_map = {s: slug_to_short.get(s, s) for s in matrix.columns}
        matrix = matrix.rename(index=rename_map, columns=rename_map)
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
