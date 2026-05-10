"""Settings → Risk-metrics cache section: manual recompute (stale-only / full)."""

from __future__ import annotations

import streamlit as st

from services.mf_metrics import recompute_metrics, recompute_stale_metrics
from ui.state.loaders import load_metrics_cached


def render() -> None:
    st.divider()
    st.subheader("Risk-metrics cache")
    st.caption(
        "Per-scheme metrics (CAGR, Vol, Sharpe, Sortino, Calmar, VaR/CVaR, etc.) are pre-"
        "computed and stored in `mf_scheme_metrics` so the Screener and Risk-vs-Return pages "
        "render with a single SELECT instead of running quantstats on every Streamlit rerun. "
        "Recompute happens automatically after every NAV sync — these buttons are for ad-hoc "
        "manual triggers."
    )

    mc1, mc2 = st.columns(2)
    with mc1:
        if st.button(
            "Recompute stale only",
            help="Schemes whose latest NAV is newer than their cached metric.",
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
        ):
            with st.spinner("Recomputing all metrics…"):
                n = recompute_metrics()
            load_metrics_cached.clear()
            st.success(f"Updated metrics for {n} scheme(s).")
