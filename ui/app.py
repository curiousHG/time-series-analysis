from pathlib import Path

import streamlit as st

import ui.charts.theme  # noqa: F401 — registers Plotly dark theme
from core.database import init_schema
from core.logging_config import setup_logging
from core.timing import timed
from ui.components.chrome import inject_global_css

with timed("boot.setup_logging"):
    setup_logging()
with timed("boot.init_schema"):
    init_schema()


_BOOT_REFRESH_KEY = "stock_data_refresh"
_BOOT_GUARD = Path("data/user/.last_boot_refresh")


def _boot_refreshed_today() -> bool:
    import datetime  # noqa: PLC0415

    try:
        return _BOOT_GUARD.read_text().strip() == datetime.date.today().isoformat()
    except OSError:
        return False


@st.cache_resource(show_spinner=False)
def _kickoff_background_refresh() -> bool:
    """Start the once-per-day background market-data refresh under the shared stock-refresh key."""
    import datetime  # noqa: PLC0415 — keep boot imports minimal
    import logging  # noqa: PLC0415

    from core.background import start_task  # noqa: PLC0415

    if _boot_refreshed_today():
        return True

    def _work() -> dict:
        from services.stock_sync_service import refresh_all_stock_data, seed_nifty500  # noqa: PLC0415

        seed_nifty500()
        result = refresh_all_stock_data()
        _BOOT_GUARD.parent.mkdir(parents=True, exist_ok=True)
        _BOOT_GUARD.write_text(datetime.date.today().isoformat())
        logging.getLogger("boot").info("boot data refresh done: %s", result)
        return result

    start_task(_BOOT_REFRESH_KEY, _work)
    return True


def run():
    with timed("page.run"):
        st.set_page_config(layout="wide")
        _kickoff_background_refresh()
        pages = [
            st.Page("ui/views/overview/page.py", title="Overview", url_path="overview", default=True),
            st.Page("ui/views/portfolio/page.py", title="Portfolio", url_path="portfolio"),
            st.Page("ui/views/mutual_fund/page.py", title="Mutual Fund Analysis", url_path="mutual-fund"),
            st.Page("ui/views/mf_screener/page.py", title="MF Screener", url_path="screener"),
            st.Page("ui/views/stock_analysis/page.py", title="Stock Analysis", url_path="stock"),
            st.Page("ui/views/stock_screener/page.py", title="Stock Screener", url_path="stock-screener"),
            st.Page("ui/views/backtest_lab/page.py", title="Backtest Lab", url_path="backtest"),
            st.Page("ui/views/settings/page.py", title="Settings", url_path="settings"),
        ]
        pg = st.navigation(pages, position="top")
        inject_global_css()
        pg.run()
