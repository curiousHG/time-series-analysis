import pandas as pd
import plotly.graph_objects as go


def fund_trade_comp(
    fund_df: pd.DataFrame, scheme_nav_df: pd.DataFrame, schemeName: str, symbol: str
) -> go.Figure:

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=fund_df["trade_date"],
            y=fund_df["price"],
            # use a downward red triangle for markers offset from the line to indicate actual trade prices
            mode="markers",
            marker=dict(symbol="triangle-down", color="Red", size=15),
            name="Actual Price"
        )
    )
    fig.add_trace(
        go.Scatter(
            x=scheme_nav_df["date"],
            y=scheme_nav_df["nav"],
            mode="lines",
            marker=dict(color="Blue"),
            name="NAV from advisorkhoj",
        )
    )
    fig.update_layout(
        title=f"Price vs NAV Comparison for {schemeName} ({symbol})",
        height=400,
        margin=dict(l=20, r=20, t=30, b=20),
        yaxis_title="Value",
        xaxis_title="Date",
    )

    return fig
