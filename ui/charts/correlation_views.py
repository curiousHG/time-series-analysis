"""Plotly charts for the Correlation tab — clustered heatmap + rolling pair correlation."""

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go


def plot_clustered_heatmap(corr: pd.DataFrame, ordered: list[str], title: str):
    """Reorder corr by `ordered` and render as a heatmap. Labels come from corr's index/columns."""
    keep = [n for n in ordered if n in corr.columns]
    reordered = corr.loc[keep, keep]
    fig = px.imshow(
        reordered,
        text_auto=".2f",
        aspect="auto",
        color_continuous_scale="RdBu",
        zmin=-1,
        zmax=1,
        origin="lower",
        title=title,
    )
    return fig.update_layout(xaxis_title="Fund", yaxis_title="Fund")


def plot_rolling_corr(series: pd.Series, label_a: str, label_b: str, window: int):
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=series.index, y=series.values, mode="lines", name=f"{window}-day rolling corr"))
    for y, color in [(0.7, "#10b981"), (-0.7, "#ef4444"), (0.0, "#94a3b8")]:
        fig.add_hline(y=y, line_dash="dash", line_color=color, opacity=0.5)
    fig.update_layout(
        title=f"Rolling correlation ({window}d) — {label_a} vs {label_b}",
        xaxis_title="Date",
        yaxis_title="Correlation",
        yaxis=dict(range=[-1, 1]),
    )
    return fig
