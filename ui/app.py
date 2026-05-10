import streamlit as st

import ui.charts.theme  # noqa: F401 — registers Plotly dark theme
from core.database import init_schema
from core.logging_config import setup_logging
from core.timing import timed

with timed("boot.setup_logging"):
    setup_logging()
with timed("boot.init_schema"):
    init_schema()


def run():
    with timed("page.run"):
        st.set_page_config(layout="wide")
        pages = [
            st.Page("ui/views/portfolio/page.py", title="Portfolio", url_path="portfolio"),
            st.Page("ui/views/mutual_fund/page.py", title="Mutual Fund Analysis"),
            st.Page("ui/views/mf_screener/page.py", title="MF Screener", url_path="screener"),
            st.Page("ui/views/stock_analysis/page.py", title="Stock Analysis", url_path="stock"),
            st.Page("ui/views/settings/page.py", title="Settings", url_path="settings"),
        ]
        pg = st.navigation(pages, position="top")
        pg.run()
