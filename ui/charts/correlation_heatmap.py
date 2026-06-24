import plotly.express as px


def render_correlation_heatmap(corr_df):
    return px.imshow(
        corr_df,
        text_auto=".2f",
        aspect="auto",
        color_continuous_scale="RdBu",
        origin="lower",
        title="Mutual Fund Correlation",
    ).update_layout(xaxis_title="Fund", yaxis_title="Fund")
