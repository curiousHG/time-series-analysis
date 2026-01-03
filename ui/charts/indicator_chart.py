import plotly.express as px
import streamlit as st
import pandas as pd

def render_indicator(name, indicator):
    df = pd.DataFrame({
        "Date": indicator.index,
        "value": indicator.values
    })

    fig = px.line(
        df,
        x="Date",
        y="value",
        title=name
    )

    st.plotly_chart(fig, width="stretch")

