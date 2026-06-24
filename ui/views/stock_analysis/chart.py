"""Chart tab — Plotly candlestick with indicator overlays + panels.

Full history is plotted; the view opens focused on the last year (zoom/pan or
double-click to autoscale for the whole series). Candle interval (Daily / Weekly /
Monthly) is chosen upstream in page.py via `resample_ohlc`.
"""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from indicators import INDICATOR_REGISTRY

_OVERLAY_COLORS = ["#6366f1", "#f59e0b", "#10b981", "#ef4444", "#8b5cf6", "#06b6d4"]
_PANEL_COLORS = ["#6366f1", "#f59e0b", "#10b981", "#ef4444"]
_UP, _DOWN = "#26a69a", "#ef5350"
_BG, _FG, _GRID = "#0f1117", "#e2e8f0", "#1e293b"

# pandas resample rules — "MS"/"W" are valid across pandas versions (avoids the "M" deprecation).
_RESAMPLE_RULE = {"Weekly": "W", "Monthly": "MS"}


def resample_ohlc(sdf: pd.DataFrame, interval: str) -> pd.DataFrame:
    """Aggregate daily OHLCV into the chosen candle interval (Daily = unchanged)."""
    if interval == "Daily" or interval not in _RESAMPLE_RULE:
        return sdf.reset_index(drop=True)
    d = sdf.copy()
    d["Date"] = pd.to_datetime(d["Date"])
    agg = (
        d.set_index("Date")
        .resample(_RESAMPLE_RULE[interval])
        .agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"})
        .dropna(subset=["Open", "High", "Low", "Close"])
        .reset_index()
    )
    agg["time"] = agg["Date"].dt.strftime("%Y-%m-%d")
    return agg


def render(sdf: pd.DataFrame, overlays: dict, panels: dict, selected_panels: list[str], symbol: str):
    df = sdf.reset_index(drop=True)
    dates = pd.to_datetime(df["Date"])
    has_volume = "Volume" in df.columns and bool(df["Volume"].notna().any())

    # Rows: price (tall) + optional volume + one per panel. Weights → normalized heights.
    specs = [("price", 3.0)]
    if has_volume:
        specs.append(("volume", 1.0))
    specs += [(p, 1.5) for p in selected_panels]
    total_w = sum(w for _, w in specs)
    row_of = {name: i + 1 for i, (name, _) in enumerate(specs)}

    fig = make_subplots(
        rows=len(specs),
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.02,
        row_heights=[w / total_w for _, w in specs],
    )

    fig.add_trace(
        go.Candlestick(
            x=dates,
            open=df["Open"],
            high=df["High"],
            low=df["Low"],
            close=df["Close"],
            name=symbol,
            increasing_line_color=_UP,
            decreasing_line_color=_DOWN,
            increasing_fillcolor=_UP,
            decreasing_fillcolor=_DOWN,
        ),
        row=1,
        col=1,
    )

    for idx, (name, values) in enumerate(overlays.items()):
        fig.add_trace(
            go.Scatter(
                x=dates,
                y=values,
                mode="lines",
                name=name,
                line=dict(color=_OVERLAY_COLORS[idx % len(_OVERLAY_COLORS)], width=1.2),
            ),
            row=1,
            col=1,
        )

    if has_volume:
        vol_colors = [_UP if c >= o else _DOWN for o, c in zip(df["Open"], df["Close"], strict=False)]
        fig.add_trace(
            go.Bar(x=dates, y=df["Volume"], marker_color=vol_colors, name="Volume", showlegend=False),
            row=row_of["volume"],
            col=1,
        )
        fig.update_yaxes(title_text="Vol", row=row_of["volume"], col=1)

    # Panel indicators — recompute per indicator so multi-series (MACD etc.) group together.
    for ind_name in selected_panels:
        result = INDICATOR_REGISTRY[ind_name]["fn"](df)
        r = row_of[ind_name]
        for i, (series_name, values) in enumerate(result.items()):
            if series_name == "Histogram":
                fig.add_trace(
                    go.Bar(
                        x=dates,
                        y=values,
                        name=series_name,
                        marker_color=[_UP if v >= 0 else _DOWN for v in values.fillna(0)],
                        showlegend=False,
                    ),
                    row=r,
                    col=1,
                )
            else:
                fig.add_trace(
                    go.Scatter(
                        x=dates,
                        y=values,
                        mode="lines",
                        name=series_name,
                        line=dict(color=_PANEL_COLORS[i % len(_PANEL_COLORS)], width=1.3),
                    ),
                    row=r,
                    col=1,
                )
        if ind_name == "RSI":
            for level in (30, 70):
                fig.add_hline(y=level, line_dash="dash", line_color="#475569", row=r, col=1)
        fig.update_yaxes(title_text=ind_name, row=r, col=1)

    # Open focused on the last year; y fit to that window so candles aren't squashed by
    # all-time extremes. Double-click / autoscale reveals the full series.
    last = dates.max()
    start = max(last - pd.DateOffset(years=1), dates.min())
    fig.update_xaxes(range=[start, last], gridcolor=_GRID)
    fig.update_yaxes(gridcolor=_GRID)
    window = df[dates >= start]
    if not window.empty:
        lo, hi = float(window["Low"].min()), float(window["High"].max())
        pad = (hi - lo) * 0.05 or abs(hi) * 0.01
        fig.update_yaxes(range=[lo - pad, hi + pad], row=1, col=1)

    fig.update_layout(
        height=420 + (120 if has_volume else 0) + 180 * len(selected_panels),
        margin=dict(l=40, r=20, t=20, b=20),
        paper_bgcolor=_BG,
        plot_bgcolor=_BG,
        font=dict(color=_FG),
        xaxis_rangeslider_visible=False,
        dragmode="pan",
        legend=dict(orientation="h", y=1.02, yanchor="bottom"),
    )

    st.subheader(symbol)
    st.plotly_chart(fig, use_container_width=True, key=f"chart_{symbol}")
