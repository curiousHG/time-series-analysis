"""Shared risk/return scatter — one parametrized Plotly builder for every quadrant chart.

Callers own data assembly (axis resolution, filtering, scaling); this module owns the
figure: bubbles, colouring, reference lines, optional Pareto frontier, optional highlight
marker. Replaces the per-page copies in the MF screener, stock screener and portfolio.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import streamlit as st

from ui.charts.theme import GRID_COLOR, NEUTRAL, WARNING

if TYPE_CHECKING:
    import pandas as pd


def render_scatter(
    pdf: pd.DataFrame,
    *,
    x_col: str,
    y_col: str,
    x_title: str,
    y_title: str,
    key: str,
    size_col: str | None = None,
    size_max: int = 32,
    color_col: str | None = None,
    color_discrete_map: dict[str, str] | None = None,
    color_continuous: bool = False,
    color_label: str | None = None,
    hover_name_col: str | None = None,
    hover_cols: tuple[str, ...] = (),
    hline: float | None = 0.0,
    vline: float | None = 0.0,
    frontier: bool = False,
    frontier_name_col: str | None = None,
    highlight: dict[str, Any] | None = None,
    height: int = 560,
) -> None:
    """Render the scatter.

    :param color_continuous: diverging RdYlGn scale centred at 0 (e.g. Sharpe colouring);
        otherwise `color_col` is treated as categorical (with optional `color_discrete_map`).
    :param frontier: overlay the Pareto frontier of the (x, y) cloud (max y per running x).
    :param highlight: optional star marker, e.g. the portfolio point:
        `{"x": .., "y": .., "name": "Portfolio"}`.
    """
    import plotly.express as px  # noqa: PLC0415 — heavy viz deps; deferred to chart-render time

    color_kwargs: dict[str, Any] = {}
    if color_col is not None:
        color_kwargs["color"] = color_col
        if color_continuous:
            color_kwargs.update(color_continuous_scale="RdYlGn", color_continuous_midpoint=0)
        elif color_discrete_map is not None:
            color_kwargs["color_discrete_map"] = color_discrete_map
        if color_label:
            color_kwargs["labels"] = {color_col: color_label}

    fig = px.scatter(
        pdf,
        x=x_col,
        y=y_col,
        size=size_col,
        size_max=size_max,
        hover_name=hover_name_col,
        hover_data=[c for c in hover_cols if c in pdf.columns],
        **color_kwargs,
    )
    fig.update_layout(
        xaxis_title=x_title,
        yaxis_title=y_title,
        height=height,
        margin={"l": 60, "r": 20, "t": 40, "b": 50},
        legend={"orientation": "h", "y": 1.02, "yanchor": "bottom"},
    )

    if hline is not None:
        fig.add_hline(y=hline, line_color=NEUTRAL, line_dash="dot", line_width=1)
    if vline is not None:
        fig.add_vline(x=vline, line_color=NEUTRAL, line_dash="dot", line_width=1)

    if frontier:
        _add_pareto_frontier(fig, pdf, x_col=x_col, y_col=y_col, name_col=frontier_name_col)

    if highlight is not None:
        _add_highlight(fig, highlight)

    st.plotly_chart(fig, use_container_width=True, key=key)


def _add_pareto_frontier(fig, pdf: pd.DataFrame, *, x_col: str, y_col: str, name_col: str | None) -> None:
    """Pareto frontier: walk left-to-right, connecting points that beat the running max-Y."""
    import plotly.graph_objects as go  # noqa: PLC0415 — heavy viz dep; deferred to chart-render time

    sorted_pts = pdf.sort_values(x_col).reset_index(drop=True)
    front_x: list[float] = []
    front_y: list[float] = []
    front_names: list[str] = []
    running_max = -float("inf")
    for _, r in sorted_pts.iterrows():
        if r[y_col] > running_max:
            running_max = r[y_col]
            front_x.append(r[x_col])
            front_y.append(r[y_col])
            front_names.append(str(r.get(name_col, "")) if name_col else "")
    if len(front_x) < 2:
        return
    fig.add_trace(
        go.Scatter(
            x=front_x,
            y=front_y,
            mode="lines+markers",
            line={"color": WARNING, "width": 2, "dash": "dash"},
            marker={"color": WARNING, "size": 12, "symbol": "diamond", "line": {"color": GRID_COLOR, "width": 1}},
            name="Efficient frontier",
            hovertext=front_names,
            hovertemplate="<b>%{hovertext}</b><br>(risk=%{x:.2f}, return=%{y:.2f})<extra></extra>",
        )
    )


def _add_highlight(fig, highlight: dict[str, Any]) -> None:
    """Star marker for a single emphasised point (e.g. the portfolio itself)."""
    import plotly.graph_objects as go  # noqa: PLC0415 — heavy viz dep; deferred to chart-render time

    fig.add_trace(
        go.Scatter(
            x=[highlight["x"]],
            y=[highlight["y"]],
            mode="markers+text",
            marker={"color": highlight.get("color", WARNING), "size": 18, "symbol": "star"},
            text=[highlight.get("name", "")],
            textposition="top center",
            name=highlight.get("name", "Highlight"),
            showlegend=False,
        )
    )
