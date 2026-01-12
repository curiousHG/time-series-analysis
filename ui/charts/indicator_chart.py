import plotly.express as px
import pandas as pd


def render_indicator(name, indicator):
    df = pd.DataFrame({"Date": indicator.index, "value": indicator.values})

    return px.line(df, x="Date", y="value", title=name)
