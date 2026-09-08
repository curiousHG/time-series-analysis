"""Settings → Maintenance: full rebuild of the cached fund metrics. Stale rows are already
recomputed after every NAV save, so only a model change needs this."""

from __future__ import annotations

import streamlit as st

from services.mf_metrics import recompute_metrics
from ui.state.loaders import load_metrics_cached


def render() -> None:
    label, action = st.columns([8, 4], vertical_alignment="center")
    label.markdown(
        "**Rebuild all fund metrics**  \nRecomputes every scheme's cached risk and return metrics. Slow. Only needed after a metric definition changes."
    )
    if action.button("Rebuild metrics", key="metrics_rebuild_all", use_container_width=True):
        with st.spinner("Recomputing all metrics…"):
            n = recompute_metrics()
        load_metrics_cached.clear()
        st.toast(f"Recomputed metrics for {n:,} schemes.", icon="✅")
