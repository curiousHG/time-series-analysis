"""Alerts panel — severity-grouped insights with deep-links to the relevant page."""

from __future__ import annotations

from typing import TYPE_CHECKING

import streamlit as st

from ui.state import navigation
from ui.state.loaders import load_overview_alerts_cached

if TYPE_CHECKING:
    from services.insights_service import Alert

_BOX = {"critical": st.error, "warn": st.warning, "info": st.info}
_ICON = {"critical": "🚨", "warn": "⚠️", "info": "\N{INFORMATION SOURCE}️"}


def _render_alert(alert: Alert, idx: int) -> None:
    _BOX[alert.severity](alert.message, icon=_ICON[alert.severity])
    if alert.scheme_name:
        if st.button("Open in MF Analysis →", key=f"alert-open-{idx}"):
            navigation.open_fund_in_analysis(alert.scheme_name)
    elif alert.kind == "drawdown":
        st.page_link(navigation.PORTFOLIO_PAGE, label="Open Portfolio →")
    elif alert.kind == "staleness":
        st.page_link(navigation.SETTINGS_PAGE, label="Refresh data in Settings →")


def render() -> None:
    st.subheader("Alerts")
    alerts = load_overview_alerts_cached()
    if not alerts:
        st.success("No alerts — funds are tracking their benchmarks and data is fresh.", icon="✅")
        return

    counts = {s: sum(1 for a in alerts if a.severity == s) for s in ("critical", "warn", "info")}
    st.caption(" · ".join(f"{_ICON[s]} {n}" for s, n in counts.items() if n))
    for i, alert in enumerate(alerts):
        _render_alert(alert, i)
