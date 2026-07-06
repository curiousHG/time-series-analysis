"""Chart tab — TradingView Lightweight Charts candlestick with indicator overlays + panes.

Uses the `streamlit-lightweight-charts` component (proper financial charting: crosshair, scroll/zoom,
fixed bar spacing) instead of a static Plotly figure. Layout: a price pane (candles + overlay lines),
an optional volume pane, and one stacked pane per selected panel indicator (RSI/MACD/…). Candle
interval (Daily/Weekly/Monthly) is chosen upstream via `resample_ohlc`.
"""

import pandas as pd
import streamlit as st
from streamlit_lightweight_charts import renderLightweightCharts

from indicators import INDICATOR_REGISTRY

_OVERLAY_COLORS = ["#6366f1", "#f59e0b", "#10b981", "#ef4444", "#8b5cf6", "#06b6d4"]
_PANEL_COLORS = ["#6366f1", "#f59e0b", "#10b981", "#ef4444"]
_UP, _DOWN = "#26a69a", "#ef5350"
_BG, _FG, _GRID = "#0f1117", "#e2e8f0", "#1e293b"

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


def _chart_opts(height: int) -> dict:
    """Shared dark-theme chart options (a TradingView-style pane)."""
    return {
        "height": height,
        "layout": {"background": {"type": "solid", "color": _BG}, "textColor": _FG},
        "grid": {"vertLines": {"color": _GRID}, "horzLines": {"color": _GRID}},
        "timeScale": {"borderColor": _GRID, "barSpacing": 7, "rightOffset": 4},
        "rightPriceScale": {"borderColor": _GRID},
        "crosshair": {"mode": 0},
    }


def _line(times, values, color: str, title: str) -> dict:
    data = [{"time": t, "value": float(v)} for t, v in zip(times, values, strict=False) if pd.notna(v)]
    return {
        "type": "Line",
        "data": data,
        "options": {"color": color, "lineWidth": 1, "priceLineVisible": False, "lastValueVisible": False, "title": title},
    }


def render(sdf: pd.DataFrame, overlays: dict, panels: dict, selected_panels: list[str], symbol: str):
    df = sdf.reset_index(drop=True)
    if "time" not in df.columns:
        df = df.assign(time=pd.to_datetime(df["Date"]).dt.strftime("%Y-%m-%d"))
    times = df["time"]
    has_volume = "Volume" in df.columns and bool(df["Volume"].notna().any())

    # ---- Price pane: candles (or a close line when OHLC is incomplete) + overlay lines ----
    candles = [
        {"time": t, "open": float(o), "high": float(h), "low": float(low), "close": float(c)}
        for t, o, h, low, c in zip(times, df["Open"], df["High"], df["Low"], df["Close"], strict=False)
        if pd.notna(o) and pd.notna(h) and pd.notna(low) and pd.notna(c)
    ]
    if candles:
        price = {
            "type": "Candlestick",
            "data": candles,
            "options": {"upColor": _UP, "downColor": _DOWN, "wickUpColor": _UP, "wickDownColor": _DOWN, "borderVisible": False},
        }
    else:
        # Some indices (e.g. "CNX 100 Equal Weight") only carry close values — fall back to a line.
        price = _line(times, df["Close"], _UP, "Close")
        price["options"]["lineWidth"] = 2
    price_series = [
        price,
        *[
            _line(times, values, _OVERLAY_COLORS[i % len(_OVERLAY_COLORS)], name)
            for i, (name, values) in enumerate(overlays.items())
        ],
    ]
    charts = [{"chart": _chart_opts(430), "series": price_series}]

    # ---- Volume pane ----
    if has_volume:
        vol = [
            {"time": t, "value": float(v), "color": (_UP if (pd.notna(o) and pd.notna(c) and c >= o) else _DOWN)}
            for t, o, c, v in zip(times, df["Open"], df["Close"], df["Volume"], strict=False)
            if pd.notna(v)
        ]
        charts.append(
            {"chart": _chart_opts(120), "series": [{"type": "Histogram", "data": vol, "options": {"priceFormat": {"type": "volume"}}}]}
        )

    # ---- Panel panes (recompute per indicator so multi-series like MACD group together) ----
    for ind_name in selected_panels:
        result = INDICATOR_REGISTRY[ind_name]["fn"](df)
        series = []
        for i, (series_name, values) in enumerate(result.items()):
            if series_name == "Histogram":
                series.append(
                    {
                        "type": "Histogram",
                        "data": [
                            {"time": t, "value": float(v), "color": (_UP if v >= 0 else _DOWN)}
                            for t, v in zip(times, values, strict=False)
                            if pd.notna(v)
                        ],
                        "options": {},
                    }
                )
            else:
                series.append(_line(times, values, _PANEL_COLORS[i % len(_PANEL_COLORS)], series_name))
        if ind_name == "RSI" and series:
            series[0]["priceLines"] = [
                {"price": 70, "color": "#475569", "lineStyle": 2, "lineWidth": 1},
                {"price": 30, "color": "#475569", "lineStyle": 2, "lineWidth": 1},
            ]
        opts = _chart_opts(150)
        opts["watermark"] = {"visible": True, "text": ind_name, "color": "rgba(148,163,184,0.25)", "fontSize": 12, "horzAlign": "left", "vertAlign": "top"}
        charts.append({"chart": opts, "series": series})

    st.subheader(symbol)
    renderLightweightCharts(charts, key=f"lwc_{symbol}")
