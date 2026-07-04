import streamlit as st

import ui.charts.theme  # noqa: F401 — registers Plotly dark theme
from core.database import init_schema
from core.logging_config import setup_logging
from core.timing import timed

with timed("boot.setup_logging"):
    setup_logging()
with timed("boot.init_schema"):
    init_schema()


@st.cache_resource(show_spinner=False)
def _kickoff_nifty500_seed() -> bool:
    """Start a one-time background thread that seeds the Nifty 500 into the DB (OHLCV + CAPM
    metrics). DB-first + resumable, so it's a quick no-op once populated. Never blocks the UI."""
    import logging  # noqa: PLC0415 — keep boot imports minimal
    import threading  # noqa: PLC0415

    def _worker() -> None:
        try:
            from services.stock_sync_service import seed_nifty500  # noqa: PLC0415 — defer heavy import off boot

            seed_nifty500()
        except Exception:
            logging.getLogger("boot").exception("nifty500 background seed failed")

    threading.Thread(target=_worker, name="nifty500-seed", daemon=True).start()
    return True


def run():
    with timed("page.run"):
        st.set_page_config(layout="wide")
        _kickoff_nifty500_seed()  # background Nifty 500 seed (cached → starts once per process)
        pages = [
            st.Page("ui/views/overview/page.py", title="Overview", url_path="overview", default=True),
            st.Page("ui/views/portfolio/page.py", title="Portfolio", url_path="portfolio"),
            st.Page("ui/views/mutual_fund/page.py", title="Mutual Fund Analysis", url_path="mutual-fund"),
            st.Page("ui/views/mf_screener/page.py", title="MF Screener", url_path="screener"),
            st.Page("ui/views/stock_analysis/page.py", title="Stock Analysis", url_path="stock"),
            st.Page("ui/views/stock_screener/page.py", title="Stock Screener", url_path="stock-screener"),
            st.Page("ui/views/settings/page.py", title="Settings", url_path="settings"),
        ]
        pg = st.navigation(pages, position="top")
        pg.run()
