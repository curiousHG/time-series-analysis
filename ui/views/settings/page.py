"""Settings page — data inputs, freshness and maintenance."""

from __future__ import annotations

import streamlit as st

from ui.components.chrome import page_header
from ui.views.settings import amfi, data_sources, db_stats, metrics_cache, refresh, stocks, tradebook


def _render_tabs() -> None:
    data_tab, maintenance_tab, reference_tab = st.tabs(["Data", "Maintenance", "Reference"])

    with data_tab:
        tradebook.render()
        st.divider()
        refresh.render_status()
        st.divider()
        stocks.render_status()
        st.divider()
        amfi.render()

    with maintenance_tab:
        st.caption("Rare, slow jobs. Routine refreshes on the Data tab already keep everything current.")
        metrics_cache.render()
        stocks.render_maintenance()
        refresh.render_per_fund_status()
        st.divider()
        db_stats.render()

    with reference_tab:
        data_sources.render()


page_header("Settings", "Data inputs, freshness and maintenance")
_render_tabs()
