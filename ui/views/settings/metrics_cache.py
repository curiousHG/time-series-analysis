"""Settings → Risk-metrics cache section: manual recompute (stale-only / full)."""

from __future__ import annotations

import streamlit as st

from services.mf_metrics import recompute_metrics, recompute_stale_metrics
from ui.state.loaders import load_metrics_cached


def render() -> None:
    st.markdown("#### Risk Metrics Cache")
    st.caption(
        "Rebuild derived metrics for the Screener and Risk-vs-Return views. NAV sync already updates stale rows."
    )

    mc1, mc2 = st.columns(2)
    with mc1:
        if st.button(
            "Recompute stale only",
            help="Schemes whose latest NAV is newer than their cached metric.",
            use_container_width=True,
        ):
            with st.spinner("Recomputing stale metrics…"):
                n = recompute_stale_metrics()
            load_metrics_cached.clear()
            if n:
                st.success(f"Updated metrics for {n} scheme(s).")
            else:
                st.info("Cache is up to date — nothing to recompute.")

    with mc2:
        if st.button(
            "Recompute ALL",
            help="Full rebuild across every scheme with NAV history. Slow — use after model changes only.",
            use_container_width=True,
        ):
            with st.spinner("Recomputing all metrics…"):
                n = recompute_metrics()
            load_metrics_cached.clear()
            st.success(f"Updated metrics for {n} scheme(s).")
