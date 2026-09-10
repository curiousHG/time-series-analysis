"""ML diagnostic chart builders for the run Detail expander — walk-forward importance, per-window
metrics, prediction distribution and coverage."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

from ui.charts import theme
from ui.charts.theme import with_alpha

_MARGIN = {"l": 48, "r": 16, "t": 16, "b": 36}


def feature_importance_bar(importance: pd.DataFrame, *, top_n: int = 20, height: int = 420) -> go.Figure:
    """Mean importance across walk-forward windows (top `top_n` features) with the std as error bars.

    Reads the frame `strategies.ml.diagnostics` builds: index of feature names, an `importance`
    column holding the across-window mean and a `std` column."""
    top = importance.sort_values("importance", ascending=False).head(top_n).iloc[::-1]
    fig = go.Figure(
        go.Bar(
            x=top["importance"],
            y=top.index.astype(str),
            orientation="h",
            marker={"color": theme.ACCENT},
            error_x={"type": "data", "array": top["std"], "color": theme.BENCHMARK_LINE, "thickness": 1},
            hovertemplate="%{y}<br>%{x:.4f} ± %{error_x.array:.4f}<extra></extra>",
        )
    )
    fig.update_layout(
        height=height,
        margin={**_MARGIN, "l": 8},
        showlegend=False,
        xaxis={"title": None},
        yaxis={"title": None, "tickfont": {"size": 11}},
    )
    return fig


def window_metrics_bar(windows: pd.DataFrame, metric: str, *, height: int = 280) -> go.Figure:
    """One bar per walk-forward window (x = test_start) for `metric`, signed colours and a zero line."""
    values = windows[metric]
    fig = go.Figure(
        go.Bar(
            x=pd.to_datetime(windows["test_start"]),
            y=values,
            marker={"color": [theme.POSITIVE if v >= 0 else theme.NEGATIVE for v in values]},
            hovertemplate="Window from %{x|%d %b %Y}<br>" + metric + " %{y:.3f}<extra></extra>",
        )
    )
    fig.add_hline(y=0, line={"color": theme.NEUTRAL, "width": 1})
    fig.update_layout(
        height=height,
        margin=_MARGIN,
        showlegend=False,
        xaxis={"title": None},
        yaxis={"title": metric},
    )
    return fig


def prediction_histogram(
    predictions: pd.DataFrame, *, thresholds: tuple[float, ...] = (), height: int = 280
) -> go.Figure:
    """Distribution of the model's `pred` column with a vertical line at each decision threshold."""
    fig = go.Figure(
        go.Histogram(
            x=predictions["pred"].dropna(),
            nbinsx=60,
            marker={"color": theme.ACCENT, "line": {"color": theme.BG_COLOR, "width": 1}},
            hovertemplate="%{x:.3f}<br>%{y} rows<extra></extra>",
        )
    )
    for t in thresholds:
        fig.add_vline(x=t, line={"color": theme.WARNING, "width": 1, "dash": "dash"})
    fig.update_layout(
        height=height,
        margin=_MARGIN,
        showlegend=False,
        xaxis={"title": None},
        yaxis={"title": None},
    )
    return fig


def coverage_area(coverage: pd.Series, height: int = 220) -> go.Figure:
    """Share of the universe with a live prediction over time (0 to 1), as a filled area."""
    coverage = coverage.sort_index()
    fig = go.Figure(
        go.Scatter(
            x=coverage.index,
            y=coverage.values,
            mode="lines",
            fill="tozeroy",
            fillcolor=with_alpha(theme.INFO, 0.18),
            line={"color": theme.INFO, "width": 1.5},
            hovertemplate="%{x|%d %b %Y}<br>%{y:.0%}<extra></extra>",
        )
    )
    fig.update_layout(
        height=height,
        margin=_MARGIN,
        showlegend=False,
        xaxis={"title": None},
        yaxis={"title": None, "tickformat": ".0%", "range": [0, 1]},
    )
    return fig
