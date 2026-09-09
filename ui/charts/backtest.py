"""Backtest Lab chart builders — every figure the run Detail / Compare views draw.

Builders take plain pandas inputs and return `go.Figure` with no in-figure title (the view puts an
`st.markdown` heading above each) and compact margins; colours come from `ui.charts.theme` only.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from ui.charts import theme
from ui.charts.theme import with_alpha

_MARGIN = {"l": 48, "r": 16, "t": 16, "b": 36}
_LEGEND_TOP = {"orientation": "h", "yanchor": "bottom", "y": 1.0, "xanchor": "left", "x": 0}
_RUPEE_AXIS = {"tickprefix": "₹", "tickformat": "~s"}


def _rebased(series: pd.Series, base: float = 100.0) -> pd.Series:
    first = series.dropna()
    if first.empty or first.iloc[0] == 0:
        return series
    return series / first.iloc[0] * base


def _aligned_benchmark(equity: pd.Series, benchmark: pd.Series, *, rebase: bool) -> pd.Series:
    """Benchmark on the equity's dates, indexed to the equity's start (or to 100) so both lines
    share one y-axis."""
    bench = benchmark.sort_index().reindex(equity.index, method="ffill").dropna()
    base = 100.0 if rebase else float(equity.dropna().iloc[0])
    return _rebased(bench, base)


def _drawdown(equity: pd.Series) -> pd.Series:
    return equity / equity.cummax() - 1


def equity_vs_benchmark(
    equity: pd.Series, benchmark: pd.Series | None, *, rebase: bool = False, height: int = 460
) -> go.Figure:
    """Equity curve (with optional benchmark on the same scale) over a filled drawdown subplot."""
    equity = equity.sort_index().dropna()
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.72, 0.28], vertical_spacing=0.04)
    eq_line = _rebased(equity) if rebase else equity
    fig.add_trace(
        go.Scatter(
            x=eq_line.index,
            y=eq_line.values,
            mode="lines",
            name="Strategy",
            line={"color": theme.ACCENT, "width": 2},
            hovertemplate="%{x|%d %b %Y}<br>Strategy %{y:,.2f}<extra></extra>",
        ),
        row=1,
        col=1,
    )
    if benchmark is not None and not benchmark.dropna().empty:
        bench = _aligned_benchmark(equity, benchmark, rebase=rebase)
        fig.add_trace(
            go.Scatter(
                x=bench.index,
                y=bench.values,
                mode="lines",
                name="Benchmark",
                line={"color": theme.BENCHMARK_LINE, "width": 1.5, "dash": "dot"},
                hovertemplate="%{x|%d %b %Y}<br>Benchmark %{y:,.2f}<extra></extra>",
            ),
            row=1,
            col=1,
        )
    dd = _drawdown(equity)
    fig.add_trace(
        go.Scatter(
            x=dd.index,
            y=dd.values,
            mode="lines",
            name="Drawdown",
            fill="tozeroy",
            fillcolor=with_alpha(theme.NEGATIVE, 0.18),
            line={"color": theme.NEGATIVE, "width": 1},
            hovertemplate="%{x|%d %b %Y}<br>Drawdown %{y:.1%}<extra></extra>",
            showlegend=False,
        ),
        row=2,
        col=1,
    )
    fig.update_layout(height=height, margin=_MARGIN, legend=_LEGEND_TOP, hovermode="x unified")
    fig.update_yaxes(title=None, row=1, col=1, **({} if rebase else _RUPEE_AXIS))
    fig.update_yaxes(title=None, tickformat=".0%", row=2, col=1)
    fig.update_xaxes(title=None)
    return fig


def equity_overlay(
    curves: dict[str, pd.Series], benchmark: pd.Series | None, *, rebase: bool = True, height: int = 420
) -> go.Figure:
    """Several runs' equity curves on one axis (rebased to 100 by default) plus an optional benchmark."""
    fig = go.Figure()
    union_index = None
    for i, (name, curve) in enumerate(curves.items()):
        curve = curve.sort_index().dropna()
        union_index = curve.index if union_index is None else union_index.union(curve.index)
        line = _rebased(curve) if rebase else curve
        fig.add_trace(
            go.Scatter(
                x=line.index,
                y=line.values,
                mode="lines",
                name=name,
                line={"color": theme.COLORS[i % len(theme.COLORS)], "width": 2},
                hovertemplate="%{x|%d %b %Y}<br>%{fullData.name} %{y:,.2f}<extra></extra>",
            )
        )
    if benchmark is not None and union_index is not None and not benchmark.dropna().empty:
        bench = benchmark.sort_index().reindex(union_index, method="ffill").dropna()
        line = _rebased(bench) if rebase else bench
        fig.add_trace(
            go.Scatter(
                x=line.index,
                y=line.values,
                mode="lines",
                name="Benchmark",
                line={"color": theme.BENCHMARK_LINE, "width": 1.5, "dash": "dot"},
                hovertemplate="%{x|%d %b %Y}<br>Benchmark %{y:,.2f}<extra></extra>",
            )
        )
    fig.update_layout(
        height=height,
        margin=_MARGIN,
        legend=_LEGEND_TOP,
        hovermode="x unified",
        xaxis={"title": None},
        yaxis={"title": None, **({} if rebase else _RUPEE_AXIS)},
    )
    return fig


def _sign_colours(values) -> list[str]:
    return [theme.POSITIVE if v >= 0 else theme.NEGATIVE for v in values]


def per_symbol_bar(
    per_symbol: pd.DataFrame, *, value_col: str = "profit_abs", top_n: int = 15, height: int = 340
) -> go.Figure:
    """Horizontal P&L per symbol — the best and worst `top_n` when the universe is larger than twice `top_n`."""
    ordered = per_symbol.sort_values(value_col)
    if len(ordered) > 2 * top_n:
        ordered = pd.concat([ordered.head(top_n), ordered.tail(top_n)])
    custom = np.stack([ordered["trades"].to_numpy(), ordered["win_rate"].to_numpy()], axis=1)
    fig = go.Figure(
        go.Bar(
            x=ordered[value_col],
            y=ordered["symbol"],
            orientation="h",
            marker={"color": _sign_colours(ordered[value_col])},
            customdata=custom,
            hovertemplate="%{y}<br>₹%{x:,.0f} · %{customdata[0]} trades · win %{customdata[1]:.0%}<extra></extra>",
        )
    )
    fig.update_layout(
        height=height,
        margin={**_MARGIN, "l": 8},
        showlegend=False,
        xaxis={"title": None, **_RUPEE_AXIS},
        yaxis={"title": None, "tickfont": {"size": 11}},
    )
    return fig


def exit_reason_bar(per_exit_reason: pd.DataFrame, *, height: int = 340) -> go.Figure:
    """P&L by exit reason with the trade count in the hover."""
    ordered = per_exit_reason.sort_values("profit_abs")
    fig = go.Figure(
        go.Bar(
            x=ordered["profit_abs"],
            y=ordered["exit_reason"],
            orientation="h",
            marker={"color": _sign_colours(ordered["profit_abs"])},
            customdata=ordered[["trades"]].to_numpy(),
            hovertemplate="%{y}<br>₹%{x:,.0f} · %{customdata[0]} trades<extra></extra>",
        )
    )
    fig.update_layout(
        height=height,
        margin={**_MARGIN, "l": 8},
        showlegend=False,
        xaxis={"title": None, **_RUPEE_AXIS},
        yaxis={"title": None},
    )
    return fig


def cost_waterfall(gross_pnl: float, costs: dict[str, float], *, height: int = 340) -> go.Figure:
    """Gross P&L stepped down by each cost bucket to the net total."""
    labels = ["Gross", *costs.keys(), "Net"]
    values = [gross_pnl, *(-abs(v) for v in costs.values()), gross_pnl - sum(abs(v) for v in costs.values())]
    measures = ["absolute", *(["relative"] * len(costs)), "total"]
    fig = go.Figure(
        go.Waterfall(
            x=labels,
            y=values,
            measure=measures,
            increasing={"marker": {"color": theme.POSITIVE}},
            decreasing={"marker": {"color": theme.NEGATIVE}},
            totals={"marker": {"color": theme.ACCENT}},
            connector={"line": {"color": theme.NEUTRAL, "width": 1}},
            hovertemplate="%{x}<br>₹%{y:,.0f}<extra></extra>",
        )
    )
    fig.update_layout(
        height=height,
        margin=_MARGIN,
        showlegend=False,
        xaxis={"title": None},
        yaxis={"title": None, **_RUPEE_AXIS},
    )
    return fig


def returns_histogram(returns: pd.Series, *, height: int = 340, bins: int = 40) -> go.Figure:
    """Daily-return distribution with losses and gains in their own colour on a shared zero-anchored grid."""
    clean = returns.dropna().astype(float)
    span = float(clean.abs().max()) if not clean.empty else 0.01
    size = max(span * 2 / bins, 1e-6)
    start = -np.ceil(span / size) * size
    xbins = {"start": start, "end": -start + size, "size": size}
    fig = go.Figure()
    for name, subset, colour in (
        ("Losses", clean[clean < 0], theme.NEGATIVE),
        ("Gains", clean[clean >= 0], theme.POSITIVE),
    ):
        fig.add_trace(
            go.Histogram(
                x=subset,
                xbins=xbins,
                name=name,
                marker={"color": colour, "line": {"color": theme.BG_COLOR, "width": 1}},
                hovertemplate="%{x:.2%}<br>%{y} days<extra></extra>",
            )
        )
    fig.update_layout(
        height=height,
        margin=_MARGIN,
        barmode="overlay",
        showlegend=False,
        xaxis={"title": None, "tickformat": ".1%"},
        yaxis={"title": None},
    )
    fig.add_vline(x=0, line={"color": theme.NEUTRAL, "width": 1, "dash": "dot"})
    return fig


def trade_markers_figure(price: pd.Series, trades: pd.DataFrame, *, height: int = 420) -> go.Figure:
    """Plotly fallback for the trade-marker chart: close line with entry/exit markers coloured by profit sign."""
    price = price.sort_index().dropna()
    fig = go.Figure(
        go.Scatter(
            x=price.index,
            y=price.values,
            mode="lines",
            name="Close",
            line={"color": theme.POSITIVE_SOFT, "width": 1.5},
            hovertemplate="%{x|%d %b %Y}<br>%{y:,.2f}<extra></extra>",
        )
    )
    colours = _sign_colours(trades["profit_abs"]) if len(trades) else []
    custom = trades[["exit_reason", "profit_abs"]].to_numpy() if len(trades) else np.empty((0, 2))
    fig.add_trace(
        go.Scatter(
            x=pd.to_datetime(trades["open_date"]),
            y=trades["open_rate"],
            mode="markers",
            name="Entry",
            marker={"symbol": "triangle-up", "size": 10, "color": colours},
            customdata=custom,
            hovertemplate="Entry %{x|%d %b %Y} @ %{y:,.2f}<br>P&L ₹%{customdata[1]:,.0f}<extra></extra>",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=pd.to_datetime(trades["close_date"]),
            y=trades["close_rate"],
            mode="markers",
            name="Exit",
            marker={"symbol": "triangle-down", "size": 10, "color": colours},
            customdata=custom,
            hovertemplate=(
                "Exit %{x|%d %b %Y} @ %{y:,.2f}<br>%{customdata[0]} · P&L ₹%{customdata[1]:,.0f}<extra></extra>"
            ),
        )
    )
    fig.update_layout(
        height=height,
        margin=_MARGIN,
        legend=_LEGEND_TOP,
        hovermode="closest",
        xaxis={"title": None},
        yaxis={"title": None},
    )
    return fig
