"""Optimiser chart builders — drawn from persisted trial records (a DataFrame), never a live study.

Expected trial columns: `number`, `value` (the objective), optional `state` (COMPLETE / PRUNED /
FAIL), optional `oos_value`, and one column per searched parameter.
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

from ui.charts import theme

_MARGIN = {"l": 48, "r": 16, "t": 16, "b": 36}
_COMPLETE = "COMPLETE"


def _split_states(trials: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if "state" not in trials.columns:
        return trials, trials.iloc[0:0]
    complete = trials["state"].astype(str).str.upper() == _COMPLETE
    return trials[complete], trials[~complete]


def _is_categorical(values: pd.Series) -> bool:
    return values.dtype == bool or not pd.api.types.is_numeric_dtype(values)


def trials_history(trials: pd.DataFrame, *, value_col: str = "value", height: int = 320) -> go.Figure:
    """Objective per trial with the running best; pruned / failed trials as hollow markers on the baseline."""
    ordered = trials.sort_values("number")
    complete, other = _split_states(ordered)
    complete = complete.dropna(subset=[value_col])
    fig = go.Figure(
        go.Scatter(
            x=complete["number"],
            y=complete[value_col],
            mode="markers",
            name="Trial",
            marker={"color": theme.ACCENT, "size": 8},
            hovertemplate="Trial %{x}<br>%{y:.3f}<extra></extra>",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=complete["number"],
            y=complete[value_col].cummax(),
            mode="lines",
            name="Best so far",
            line={"color": theme.WARNING, "width": 2, "shape": "hv"},
            hovertemplate="Best after trial %{x}: %{y:.3f}<extra></extra>",
        )
    )
    if len(other):
        floor = float(complete[value_col].min()) if len(complete) else 0.0
        fig.add_trace(
            go.Scatter(
                x=other["number"],
                y=[floor] * len(other),
                mode="markers",
                name="Pruned / failed",
                marker={"symbol": "circle-open", "color": theme.NEUTRAL, "size": 8},
                customdata=other["state"],
                hovertemplate="Trial %{x}<br>%{customdata}<extra></extra>",
            )
        )
    fig.update_layout(
        height=height,
        margin=_MARGIN,
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.0, "x": 0},
        xaxis={"title": "Trial"},
        yaxis={"title": None},
    )
    return fig


def param_importance_bar(importance: dict[str, float], height: int = 320) -> go.Figure:
    """Hyper-parameter importance, largest at the top."""
    ordered = sorted(importance.items(), key=lambda kv: kv[1])
    fig = go.Figure(
        go.Bar(
            x=[v for _, v in ordered],
            y=[k for k, _ in ordered],
            orientation="h",
            marker={"color": theme.ACCENT},
            hovertemplate="%{y}<br>%{x:.1%}<extra></extra>",
        )
    )
    fig.update_layout(
        height=height,
        margin={**_MARGIN, "l": 8},
        showlegend=False,
        xaxis={"title": None, "tickformat": ".0%"},
        yaxis={"title": None},
    )
    return fig


def _dimension(label: str, values: pd.Series) -> dict:
    if _is_categorical(values):
        labels = values.astype(str)
        categories = sorted(labels.unique())
        codes = labels.map({c: i for i, c in enumerate(categories)})
        return {
            "label": label,
            "values": codes,
            "tickvals": list(range(len(categories))),
            "ticktext": categories,
            "range": [-0.5, len(categories) - 0.5],
        }
    return {"label": label, "values": values}


def parallel_coordinates(
    trials: pd.DataFrame, params: list[str], *, value_col: str = "value", height: int = 420
) -> go.Figure:
    """One axis per searched parameter plus the objective, lines coloured by objective value."""
    complete, _ = _split_states(trials)
    complete = complete.dropna(subset=[value_col])
    dimensions = [_dimension(p, complete[p]) for p in params]
    dimensions.append({"label": value_col, "values": complete[value_col]})
    fig = go.Figure(
        go.Parcoords(
            dimensions=dimensions,
            line={
                "color": complete[value_col],
                "colorscale": [[0, theme.NEGATIVE], [0.5, theme.WARNING], [1, theme.POSITIVE]],
                "showscale": True,
                "colorbar": {"title": value_col, "thickness": 12},
            },
            labelfont={"color": theme.TEXT_COLOR, "size": 12},
            tickfont={"color": theme.TEXT_COLOR, "size": 10},
        )
    )
    fig.update_layout(height=height, margin={"l": 64, "r": 48, "t": 48, "b": 24})
    return fig


def param_slice(trials: pd.DataFrame, param: str, *, value_col: str = "value", height: int = 260) -> go.Figure:
    """Objective against one parameter, trial order carried in the hover."""
    complete, _ = _split_states(trials)
    complete = complete.dropna(subset=[value_col, param])
    x = complete[param].astype(str) if _is_categorical(complete[param]) else complete[param]
    fig = go.Figure(
        go.Scatter(
            x=x,
            y=complete[value_col],
            mode="markers",
            marker={"color": theme.ACCENT, "size": 8, "opacity": 0.8},
            customdata=complete["number"],
            hovertemplate=f"{param} %{{x}}<br>%{{y:.3f}} (trial %{{customdata}})<extra></extra>",
        )
    )
    fig.update_layout(
        height=height,
        margin=_MARGIN,
        showlegend=False,
        xaxis={"title": param},
        yaxis={"title": None},
    )
    return fig


def is_vs_oos(
    trials: pd.DataFrame, *, is_col: str = "value", oos_col: str = "oos_value", height: int = 320
) -> go.Figure:
    """In-sample vs out-of-sample objective for the re-run trials, with the y = x reference."""
    both = trials.dropna(subset=[is_col, oos_col])
    lo = float(min(both[is_col].min(), both[oos_col].min())) if len(both) else 0.0
    hi = float(max(both[is_col].max(), both[oos_col].max())) if len(both) else 1.0
    fig = go.Figure(
        go.Scatter(
            x=both[is_col],
            y=both[oos_col],
            mode="markers",
            name="Trial",
            marker={"color": theme.ACCENT, "size": 9},
            customdata=both["number"],
            hovertemplate="Trial %{customdata}<br>IS %{x:.3f} · OOS %{y:.3f}<extra></extra>",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=[lo, hi],
            y=[lo, hi],
            mode="lines",
            name="IS = OOS",
            line={"color": theme.NEUTRAL, "width": 1, "dash": "dot"},
            hoverinfo="skip",
        )
    )
    fig.update_layout(
        height=height,
        margin=_MARGIN,
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.0, "x": 0},
        xaxis={"title": "In-sample"},
        yaxis={"title": "Out-of-sample"},
    )
    return fig
