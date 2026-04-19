import streamlit as st

import ui.charts.theme  # noqa: F401 — registers Plotly dark theme
from core.logging_config import setup_logging
from core.database import init_schema

setup_logging()
init_schema()


def run():
    st.set_page_config(layout="wide")
    # st.write("🚀 App started")
    pages = [
        st.Page("ui/views/mutual_fund.py", title="Mutual Fund Analysis"),
        st.Page("ui/views/backtest.py", title="Stock Analysis", url_path="stock"),
        st.Page("ui/views/data_manager.py", title="Data Manager", url_path="data"),
    ]
    pg = st.navigation(pages, position="top")
    pg.run()
