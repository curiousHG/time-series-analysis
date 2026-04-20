"""Drawdown tab — drawdown chart, underwater chart."""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go


def render(pv: pd.DataFrame):
    pv = pv.sort_values("date").copy()
    pv["peak"] = pv["portfolio_value"].cummax()
    pv["drawdown"] = (pv["portfolio_value"] - pv["peak"]) / pv["peak"] * 100

    max_dd = pv["drawdown"].min()
    max_dd_date = pv.loc[pv["drawdown"].idxmin(), "date"]
    current_dd = pv["drawdown"].iloc[-1]

    col1, col2, col3 = st.columns(3)
    col1.metric("Max Drawdown", f"{max_dd:.2f}%")
    col2.metric("Max Drawdown Date", str(max_dd_date)[:10])
    col3.metric("Current Drawdown", f"{current_dd:.2f}%")

    # Drawdown chart
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=pv["date"], y=pv["drawdown"],
        fill="tozeroy", mode="lines", name="Drawdown",
        line=dict(color="#ef4444", width=1),
        fillcolor="rgba(239, 68, 68, 0.2)",
    ))
    fig.add_hline(y=max_dd, line_dash="dash", line_color="#94a3b8",
                  annotation_text=f"Max: {max_dd:.2f}%")
    fig.update_layout(height=350, yaxis_title="Drawdown (%)", xaxis_title="Date", hovermode="x unified")
    st.plotly_chart(fig, use_container_width=True, key="drawdown")

    # Underwater chart
    fig2 = go.Figure()
    fig2.add_trace(go.Scatter(
        x=pv["date"], y=pv["portfolio_value"],
        mode="lines", name="Portfolio", line=dict(color="#6366f1", width=2),
    ))
    fig2.add_trace(go.Scatter(
        x=pv["date"], y=pv["peak"],
        mode="lines", name="Peak", line=dict(color="#94a3b8", width=1, dash="dash"),
    ))
    fig2.update_layout(
        height=350, yaxis_title="Value (INR)", xaxis_title="Date",
        title="Portfolio vs All-Time High", hovermode="x unified",
    )
    st.plotly_chart(fig2, use_container_width=True, key="underwater")
