"""Top-of-page banner that warns when NAV / holdings data is stale."""

import streamlit as st

from services.data_freshness import (
    FreshnessReport,
    compute_holdings_freshness,
    compute_nav_freshness,
)


@st.cache_data(ttl=300, show_spinner=False)
def _cached_nav_report(scheme_names: tuple[str, ...], scheme_slugs: tuple[str, ...]) -> FreshnessReport:
    return compute_nav_freshness(list(scheme_names), list(scheme_slugs))


@st.cache_data(ttl=300, show_spinner=False)
def _cached_holdings_report(scheme_names: tuple[str, ...], scheme_slugs: tuple[str, ...]) -> FreshnessReport:
    return compute_holdings_freshness(list(scheme_names), list(scheme_slugs))


def clear_freshness_cache() -> None:
    """Drop cached freshness reports — call after a data refresh completes."""
    _cached_nav_report.clear()
    _cached_holdings_report.clear()


def _nav_line(report: FreshnessReport) -> str:
    bdays = report.max_business_days_old
    suffix = f" (oldest: {bdays} business days behind)" if bdays else ""
    return f"⚠ **NAV data is stale** for {report.stale_count} of {report.total} selected funds{suffix}."


def _holdings_line(report: FreshnessReport) -> str:
    days = report.max_days_old
    suffix = f" (oldest: {days} days behind)" if days else ""
    return f"**Holdings data is stale** for {report.stale_count} of {report.total} selected funds{suffix}."


def render_freshness_banner(scheme_names: list[str], scheme_slugs: list[str]) -> None:
    """Render an st.warning banner when any selected scheme has stale NAV or holdings data."""
    if not scheme_names:
        return

    names_key = tuple(scheme_names)
    slugs_key = tuple(scheme_slugs)

    nav_report = _cached_nav_report(names_key, slugs_key)
    holdings_report = _cached_holdings_report(names_key, slugs_key)

    if not nav_report.has_stale and not holdings_report.has_stale:
        return

    lines: list[str] = []
    if nav_report.has_stale:
        lines.append(_nav_line(nav_report))
    if holdings_report.has_stale:
        lines.append(_holdings_line(holdings_report))

    st.warning("\n\n".join(lines))
    st.page_link("ui/views/data_manager.py", label="Open Data Manager to refresh", icon="🛠")
