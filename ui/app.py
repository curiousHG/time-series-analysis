import streamlit as st

import ui.charts.theme  # noqa: F401 — registers Plotly dark theme
from core.database import init_schema
from core.logging_config import setup_logging

setup_logging()
init_schema()


def run():
    st.set_page_config(layout="wide")
    # st.write("🚀 App started")
    pages = [
        st.Page("ui/views/portfolio.py", title="Portfolio", url_path="portfolio"),
        st.Page("ui/views/mutual_fund.py", title="Mutual Fund Analysis"),
        st.Page("ui/views/stock_analysis.py", title="Stock Analysis", url_path="stock"),
        st.Page("ui/views/data_manager.py", title="Data Manager", url_path="data"),
    ]
    pg = st.navigation(pages, position="top")
    pg.run()
