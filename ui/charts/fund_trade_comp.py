

import polars as pl
import plotly.graph_objects as go

def fund_trade_comp(fund_df, scheme_nav_df: pl.DataFrame, schemeName: str, symbol: str) -> go.Figure:

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=fund_df["trade_date"],
        y=fund_df["price"],
        mode="markers",
        name="Actual Price",
        marker=dict(color='Red', size=6),
    ))
    fig.add_trace(go.Scatter(
        x=scheme_nav_df["date"],
        y=scheme_nav_df["nav"],
        mode="lines",
        name="NAV from advisorkhoj"
    ))
    fig.update_layout(
        title=f"Price vs NAV Comparison for {schemeName} ({symbol})",
        height=400,
        margin=dict(l=20, r=20, t=30, b=20),
        yaxis_title="Value",
        xaxis_title="Date",
    )

    return fig
