"""Chart tab — TradingView candlestick chart with technical indicator overlays and panels."""

import json

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots
from streamlit_lightweight_charts import renderLightweightCharts

from core.indicators import INDICATOR_REGISTRY


def render(sdf: pd.DataFrame, overlays: dict, panels: dict, selected_panels: list[str], symbol: str):
    # ---- Price chart with lightweight-charts ----
    candles = json.loads(
        sdf[["time", "Open", "High", "Low", "Close"]]
        .rename(columns={"Open": "open", "High": "high", "Low": "low", "Close": "close"})
        .dropna()
        .to_json(orient="records")
    )

    volume_data = json.loads(
        sdf[["time", "Volume"]]
        .rename(columns={"Volume": "value"})
        .dropna()
        .to_json(orient="records")
    )

    for i, row in enumerate(volume_data):
        if i < len(candles):
            row["color"] = (
                "rgba(38, 166, 154, 0.5)"
                if candles[i]["close"] >= candles[i]["open"]
                else "rgba(239, 83, 80, 0.5)"
            )

    chart_options = {
        "height": 500,
        "layout": {
            "background": {"color": "#0f1117"},
            "textColor": "#e2e8f0",
        },
        "grid": {
            "vertLines": {"color": "#1e293b"},
            "horzLines": {"color": "#1e293b"},
        },
        "crosshair": {"mode": 0},
        "timeScale": {"borderColor": "#334155"},
        "rightPriceScale": {"borderColor": "#334155"},
    }

    overlay_colors = ["#6366f1", "#f59e0b", "#10b981", "#ef4444", "#8b5cf6", "#06b6d4"]

    series = [
        {
            "type": "Candlestick",
            "data": candles,
            "options": {
                "upColor": "#26a69a",
                "downColor": "#ef5350",
                "borderUpColor": "#26a69a",
                "borderDownColor": "#ef5350",
                "wickUpColor": "#26a69a",
                "wickDownColor": "#ef5350",
            },
        },
    ]

    # Add overlay indicators to price chart
    for idx, (name, values) in enumerate(overlays.items()):
        line_data = json.loads(
            pd.DataFrame({"time": sdf["time"], "value": values})
            .dropna()
            .to_json(orient="records")
        )
        series.append({
            "type": "Line",
            "data": line_data,
            "options": {
                "color": overlay_colors[idx % len(overlay_colors)],
                "lineWidth": 1,
                "title": name,
            },
        })

    # Volume bars
    series.append({
        "type": "Histogram",
        "data": volume_data,
        "options": {
            "priceFormat": {"type": "volume"},
            "priceScaleId": "",
        },
    })

    st.subheader(symbol)
    renderLightweightCharts(
        [{"chart": chart_options, "series": series}],
        key=f"chart_{symbol}",
    )

    # ---- Panel indicators (Plotly subplots below) ----
    if panels:
        # Group panels by indicator name prefix
        panel_groups: dict[str, dict[str, pd.Series]] = {}
        for name, values in panels.items():
            # Group MACD/Signal/Histogram together
            for ind_name in selected_panels:
                if name in INDICATOR_REGISTRY.get(ind_name, {}).get("fn", lambda _: {})(sdf):
                    panel_groups.setdefault(ind_name, {})[name] = values
                    break
            else:
                panel_groups.setdefault(name, {})[name] = values

        n_panels = len(selected_panels)
        fig = make_subplots(
            rows=n_panels,
            cols=1,
            shared_xaxes=True,
            vertical_spacing=0.03,
            row_heights=[1] * n_panels,
        )

        panel_colors = ["#6366f1", "#f59e0b", "#10b981", "#ef4444"]
        panel_idx = 0

        for ind_name in selected_panels:
            entry = INDICATOR_REGISTRY[ind_name]
            result = entry["fn"](sdf)
            panel_idx += 1

            for i, (series_name, values) in enumerate(result.items()):
                if series_name == "Histogram":
                    fig.add_trace(
                        go.Bar(
                            x=sdf["Date"],
                            y=values,
                            name=series_name,
                            marker_color=[
                                "#26a69a" if v >= 0 else "#ef5350"
                                for v in values.fillna(0)
                            ],
                            showlegend=True,
                        ),
                        row=panel_idx,
                        col=1,
                    )
                else:
                    fig.add_trace(
                        go.Scatter(
                            x=sdf["Date"],
                            y=values,
                            mode="lines",
                            name=series_name,
                            line=dict(
                                color=panel_colors[i % len(panel_colors)],
                                width=1.5,
                            ),
                        ),
                        row=panel_idx,
                        col=1,
                    )

            # Add reference lines for RSI
            if ind_name == "RSI":
                for level in [30, 70]:
                    fig.add_hline(
                        y=level,
                        line_dash="dash",
                        line_color="#475569",
                        row=panel_idx,
                        col=1,
                    )

            fig.update_yaxes(title_text=ind_name, row=panel_idx, col=1)

        fig.update_layout(
            height=200 * n_panels,
            margin=dict(l=40, r=20, t=20, b=30),
            paper_bgcolor="#0f1117",
            plot_bgcolor="#0f1117",
            font=dict(color="#e2e8f0"),
            xaxis=dict(gridcolor="#1e293b"),
            legend=dict(orientation="h", y=-0.05),
        )

        # Apply grid to all axes
        for i in range(1, n_panels + 1):
            fig.update_xaxes(gridcolor="#1e293b", row=i, col=1)
            fig.update_yaxes(gridcolor="#1e293b", row=i, col=1)

        st.plotly_chart(fig, use_container_width=True, key=f"panels_{symbol}")
