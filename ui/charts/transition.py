"""Chart builders for the transition-matrix diagnostics on the run Detail view."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import plotly.graph_objects as go

from ui.charts import theme

if TYPE_CHECKING:
    import pandas as pd

_MARGIN = {"l": 110, "r": 16, "t": 16, "b": 90}
_DIVERGING = [
    [0.0, theme.NEGATIVE],
    [0.5, theme.BG_COLOR],
    [1.0, theme.POSITIVE],
]


def probability_heatmap(matrix: pd.DataFrame, *, height: int = 460) -> go.Figure:
    """P(tomorrow's bucket | today's bucket), each row a distribution that sums to one.

    Colour is centred on the uniform probability, so a cell reads as "more likely than chance"
    rather than as a raw level: with eleven buckets every cell would otherwise look small.
    """
    values = matrix.to_numpy(dtype=float)
    uniform = 1.0 / max(matrix.shape[1], 1)
    spread = float(np.nanmax(np.abs(values - uniform))) or uniform
    fig = go.Figure(
        go.Heatmap(
            z=values,
            x=[str(c) for c in matrix.columns],
            y=[str(i) for i in matrix.index],
            colorscale=_DIVERGING,
            zmid=uniform,
            zmin=uniform - spread,
            zmax=uniform + spread,
            hovertemplate="today %{y}<br>tomorrow %{x}<br>%{z:.1%}<extra></extra>",
            colorbar={"tickformat": ".0%", "thickness": 12, "title": None},
        )
    )
    fig.update_layout(
        height=height,
        margin=_MARGIN,
        xaxis={"title": "next bar", "tickangle": -45, "tickfont": {"size": 10}},
        yaxis={"title": "current bar", "autorange": "reversed", "tickfont": {"size": 10}},
    )
    return fig


def state_edge_bar(states: pd.DataFrame, *, height: int = 320) -> go.Figure:
    """How much each bucket lifts the odds of an up bar above the sample's own base rate.

    A bar above zero is a bucket worth betting on; the bar's width in observations is what the
    strategy's sample floor is guarding against, so the count rides along in the hover.
    """
    frame = states.dropna(subset=["lift_vs_base"])
    lift = frame["lift_vs_base"].astype(float)
    fig = go.Figure(
        go.Bar(
            x=frame["bucket"].astype(str),
            y=lift,
            marker={"color": [theme.POSITIVE if v >= 0 else theme.NEGATIVE for v in lift]},
            customdata=np.stack([frame["observations"], frame["p_up"], frame["mean_next_return"]], axis=-1),
            hovertemplate=(
                "%{x}<br>lift %{y:+.1%}<br>P(up) %{customdata[1]:.1%}"
                "<br>mean next %{customdata[2]:+.2%}<br>%{customdata[0]:,} observations<extra></extra>"
            ),
        )
    )
    fig.add_hline(y=0, line={"color": theme.NEUTRAL, "width": 1, "dash": "dot"})
    fig.update_layout(
        height=height,
        margin={**_MARGIN, "l": 56, "b": 110},
        showlegend=False,
        xaxis={"title": None, "tickangle": -45, "tickfont": {"size": 10}},
        yaxis={"title": "lift over base rate", "tickformat": "+.0%"},
    )
    return fig


def expected_return_bar(states: pd.DataFrame, *, height: int = 320) -> go.Figure:
    """The average next-bar return that followed each bucket, in percent."""
    frame = states.dropna(subset=["mean_next_return"])
    values = frame["mean_next_return"].astype(float)
    fig = go.Figure(
        go.Bar(
            x=frame["bucket"].astype(str),
            y=values,
            marker={"color": [theme.POSITIVE if v >= 0 else theme.NEGATIVE for v in values]},
            customdata=frame["observations"],
            hovertemplate="%{x}<br>%{y:+.2%} average next bar<br>%{customdata:,} observations<extra></extra>",
        )
    )
    fig.add_hline(y=0, line={"color": theme.NEUTRAL, "width": 1, "dash": "dot"})
    fig.update_layout(
        height=height,
        margin={**_MARGIN, "l": 56, "b": 110},
        showlegend=False,
        xaxis={"title": None, "tickangle": -45, "tickfont": {"size": 10}},
        yaxis={"title": "mean next return", "tickformat": "+.1%"},
    )
    return fig
