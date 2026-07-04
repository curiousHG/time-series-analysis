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
def _kickoff_background_refresh() -> bool:
    """Start a one-time background thread that keeps market data fresh: seed the Nifty 500 (DB-first,
    resumable), append recent NSE bhavcopy days for the whole stock universe, and pull the board
    indices to today. All cheap/idempotent, so it's a quick pass on each boot. Never blocks the UI."""
    import logging  # noqa: PLC0415 — keep boot imports minimal
    import threading  # noqa: PLC0415

    def _worker() -> None:
        log = logging.getLogger("boot")
        try:
            from services.stock_sync_service import refresh_stocks_via_bhavcopy, seed_nifty500  # noqa: PLC0415

            seed_nifty500()  # fill any missing Nifty 500 constituents (no-op once populated)
            refresh_stocks_via_bhavcopy()  # keep the whole stock universe fresh (bulk bhavcopy)
        except Exception:
            log.exception("background stock refresh failed")
        try:
            from data.repositories.stock import refresh_index_to_today  # noqa: PLC0415
            from services.insights_service import MARKET_PULSE_SYMBOLS, SECTOR_INDEX_SYMBOLS  # noqa: PLC0415

            for sym in {s for s, _, _ in SECTOR_INDEX_SYMBOLS} | set(MARKET_PULSE_SYMBOLS):
                try:
                    refresh_index_to_today(sym)
                except Exception:
                    continue
        except Exception:
            log.exception("background index refresh failed")

    threading.Thread(target=_worker, name="bg-data-refresh", daemon=True).start()
    return True


def run():
    with timed("page.run"):
        st.set_page_config(layout="wide")
        _kickoff_background_refresh()  # background data refresh (cached → starts once per process)
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
