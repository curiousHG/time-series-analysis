from pathlib import Path

import streamlit as st

import ui.charts.theme  # noqa: F401 — registers Plotly dark theme
from core.database import init_schema
from core.logging_config import setup_logging
from core.timing import timed

with timed("boot.setup_logging"):
    setup_logging()
with timed("boot.init_schema"):
    init_schema()


# Once-per-day boot-refresh guard — so restarting the process during the day doesn't re-run the
# full bhavcopy pass. Shares the "stock_data_refresh" background key with the Settings > Stock Data
# button, so the two can't run concurrently (dedup) and the boot pass is visible in that view.
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
    """Kick off a once-per-day background pass that keeps market data fresh: seed the Nifty 500
    (DB-first, resumable) + append the latest NSE bhavcopy for the whole stock+index universe +
    recompute metrics. Routed through core.background under the shared stock-refresh key so it can't
    race a user-triggered Settings refresh, and guarded to run at most once per calendar day."""
    import datetime  # noqa: PLC0415 — keep boot imports minimal
    import logging  # noqa: PLC0415

    from core.background import start_task  # noqa: PLC0415

    if _boot_refreshed_today():
        return True

    def _work() -> dict:
        from services.stock_sync_service import refresh_all_stock_data, seed_nifty500  # noqa: PLC0415

        seed_nifty500()  # fill any missing Nifty 500 constituents (no-op once populated)
        result = refresh_all_stock_data()  # bulk bhavcopy stocks + 160 indices + recompute metrics
        _BOOT_GUARD.parent.mkdir(parents=True, exist_ok=True)
        _BOOT_GUARD.write_text(datetime.date.today().isoformat())
        logging.getLogger("boot").info("boot data refresh done: %s", result)
        return result

    start_task(_BOOT_REFRESH_KEY, _work)
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
