"""Settings page — Streamlit entry. Composes six independent sections in order:
tradebook · AMFI master · risk-metrics cache · refresh tracked-fund data ·
data sources reference · DB statistics.
"""

from __future__ import annotations

import streamlit as st

from ui.views.settings import amfi, data_sources, db_stats, metrics_cache, refresh, tradebook

st.title("Settings")

tradebook.render()
amfi.render()
metrics_cache.render()
refresh.render()
data_sources.render()
db_stats.render()
