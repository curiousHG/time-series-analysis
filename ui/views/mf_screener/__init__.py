"""MF Screener page package. `page.py` is the entry point; sibling modules (filters, table,
chart, backfill) are the render layers. Data/filter logic lives in services.screener_service."""

from ui.views.mf_screener import backfill, chart, filters, table
