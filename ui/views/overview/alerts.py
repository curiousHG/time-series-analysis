"""Alerts panel — severity-grouped insights, one compact row each, with a deep-link to the
relevant page."""

from __future__ import annotations

from typing import TYPE_CHECKING

import streamlit as st

from ui.state import navigation
from ui.state.loaders import load_overview_alerts_cached

if TYPE_CHECKING:
    from services.insights_service import Alert

_DOT = {"critical": ":red[●]", "warn": ":orange[●]", "info": ":blue[●]"}
_LABEL = {"critical": "critical", "warn": "warning", "info": "notice"}


def _render_alert(alert: Alert, idx: int) -> None:
    text_col, link_col = st.columns([12, 1], vertical_alignment="center")
    text_col.markdown(f"{_DOT[alert.severity]}&nbsp; {alert.message}")
    with link_col:
        if alert.scheme_name:
            if st.button(
                "",
                icon=":material/arrow_forward:",
                key=f"alert-open-{idx}",
                type="tertiary",
                help="Open in MF Analysis",
            ):
                navigation.open_fund_in_analysis(alert.scheme_name)
        elif alert.kind == "drawdown":
            st.page_link(navigation.PORTFOLIO_PAGE, label="", icon=":material/arrow_forward:", help="Open Portfolio")
        elif alert.kind == "staleness":
            st.page_link(
                navigation.SETTINGS_PAGE, label="", icon=":material/arrow_forward:", help="Refresh in Settings"
            )


def render() -> None:
    st.subheader("Alerts")
    alerts = load_overview_alerts_cached()
    if not alerts:
        st.caption("Nothing to flag. Funds are tracking their benchmarks and data is fresh.")
        return

    counts = {s: sum(1 for a in alerts if a.severity == s) for s in ("critical", "warn", "info")}
    st.caption(" · ".join(f"{n} {_LABEL[s]}{'s' if n != 1 else ''}" for s, n in counts.items() if n))
    for i, alert in enumerate(alerts):
        _render_alert(alert, i)
