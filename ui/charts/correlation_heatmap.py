import plotly.express as px
import streamlit as st

def render_correlation_heatmap(corr_df):
    fig = px.imshow(
        corr_df,
        text_auto=".2f",
        aspect="auto",
        color_continuous_scale="RdBu",
        origin="lower",
        title="Mutual Fund Correlation"
    )

    st.plotly_chart(fig, width="stretch")