"""MF Screener page package.

`page.py` is the Streamlit entry point — see `ui/app.py`'s `st.Page("ui/views/mf_screener/page.py", ...)`.
Sibling modules (`filters`, `table`, `chart`, `backfill`) are the rendering layers that
the page orchestrator composes. Pure data assembly + filter logic lives in
`services.screener_service` so it stays Streamlit-free and testable.
"""

from ui.views.mf_screener import backfill, chart, filters, table  # noqa: F401
