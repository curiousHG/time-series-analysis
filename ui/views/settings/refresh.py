"""Settings → Funds: freshness in dates, the two routine refresh jobs, and the unresolved-source
picker. The per-fund status tables live on the Maintenance tab.

Refresh runs on a background task (stale-while-revalidate): the view keeps showing the
last-computed freshness while NAV/holdings update off-thread and swaps to fresh data when the task
finishes. Orchestration lives in services.sync_service / data_freshness / registry_service."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from data.repositories.amfi import latest_amfi_nav_date
from data.repositories.holdings import holdings_count_by_scheme
from data.repositories.nav import nav_record_stats
from mutual_funds.display import make_slug
from services.data_freshness import (
    FreshnessReport,
    build_holdings_status_rows,
    build_nav_status_rows,
    business_days_between,
    compute_holdings_freshness,
    compute_nav_freshness,
)
from services.registry_service import (
    list_active,
    list_tracked,
    list_unavailable_funds,
    retry_unavailable,
)
from services.sync_service import refresh_all_fund_data
from ui.components.background_refresh import BackgroundRefresh, progress_cb
from ui.components.chrome import section_header
from ui.components.freshness_banner import clear_freshness_cache
from ui.constants import STATUS_STYLES
from ui.state.loaders import get_short_names, load_holdings_data, load_nav_data
from ui.views.settings.format import fmt_date, plural


def _color_status(val: str) -> str:
    return STATUS_STYLES.get(val, "")


@st.cache_data(ttl=300, show_spinner="Computing fund freshness…")
def _fund_freshness() -> dict:
    """Freshness reports for every tracked fund — cached so the view keeps showing the last-known
    state while a background refresh runs (cleared when the refresh finishes)."""
    active = list_active()
    names = active["schemeName"].to_list()
    name_to_code = dict(zip(names, active["schemeCode"].to_list(), strict=True))
    slugs = [make_slug(n) for n in names]
    return {
        "names": names,
        "name_to_code": name_to_code,
        "slugs": slugs,
        "nav": compute_nav_freshness(names, slugs),
        "holdings": compute_holdings_freshness(names, slugs),
    }


def _clear_fund_caches() -> None:
    _fund_freshness.clear()
    load_nav_data.clear()
    load_holdings_data.clear()
    clear_freshness_cache()


def _fund_summary(r: object) -> str:
    parts = []
    if isinstance(r, dict):
        if "nav_updated" in r:
            parts.append(f"NAV {r['nav_updated']} updated (+{r.get('nav_new_rows', 0):,} rows)")
        if "holdings_updated" in r:
            parts.append(f"holdings {r['holdings_updated']} updated")
    return ", ".join(parts) if parts else "no changes"


_REFRESH = BackgroundRefresh(
    "fund_data_refresh", "Fund refresh", cache_clearers=(_clear_fund_caches,), summarize=_fund_summary
)


def _latest(report: FreshnessReport):
    dates = [r.last_date for r in report.rows if r.last_date is not None]
    return max(dates) if dates else None


def _nav_line(report: FreshnessReport) -> str:
    latest = _latest(report)
    if latest is None:
        return "No NAV history stored yet."
    amfi = latest_amfi_nav_date()
    if amfi is None or amfi <= latest:
        return f"NAV as of {fmt_date(latest)}, level with AMFI."
    lag = business_days_between(latest, amfi)
    return f"NAV as of {fmt_date(latest)}, {plural(lag, 'business day')} behind AMFI's {fmt_date(amfi)}."


def render_status() -> None:
    """The Funds block on the Data tab."""
    _REFRESH.consume()
    tracked = list_tracked()
    actions = section_header("Funds")
    if tracked.height == 0:
        st.markdown("No tracked funds yet. Add some from the MF Screener.")
        return

    fr = _fund_freshness()
    names = fr["names"]
    dormant = int(tracked["dormant"].sum())

    def _run(scope: str):
        return lambda: refresh_all_fund_data(
            names, name_to_code=fr["name_to_code"], scope=scope, progress_cb=progress_cb(_REFRESH.key)
        )

    with actions:
        b1, b2 = st.columns(2)
        with b1:
            _REFRESH.start_button("Update NAV", _run("nav"), meta={"scope": "nav"}, type="primary")
        with b2:
            _REFRESH.start_button("Update holdings", _run("holdings"), meta={"scope": "holdings"})
    if _REFRESH.is_running():
        _REFRESH.poll()

    st.markdown(_nav_line(fr["nav"]))
    holdings_latest = _latest(fr["holdings"])
    if holdings_latest is None:
        st.markdown("No holdings stored yet.")
    else:
        st.markdown(f"Holdings as of {fmt_date(holdings_latest)}, the latest portfolio AdvisorKhoj has published.")
    st.caption(f"{plural(len(names), 'active fund')} tracked, {plural(dormant, 'dormant scheme')} skipped.")
    _render_unresolved()


def _render_unresolved() -> None:
    unavailable = list_unavailable_funds()
    if unavailable.height == 0:
        return
    short_by_name = get_short_names(tuple(unavailable["schemeName"].to_list()))
    with st.expander(f"{plural(unavailable.height, 'fund')} have a source that could not be resolved"):
        st.caption("AdvisorKhoj or Kuvera answered with nothing for these. Retry one after the source updates.")
        pick, go = st.columns([9, 3], vertical_alignment="bottom")
        sel = pick.selectbox(
            "Fund",
            options=unavailable["schemeName"].to_list(),
            format_func=lambda n: short_by_name.get(n, n),
            key="retry_pick",
        )
        if go.button("Retry", use_container_width=True):
            with st.spinner(f"Retrying {short_by_name.get(sel, sel)}…"):
                results = retry_unavailable(sel)
            if results:
                st.toast(" · ".join(f"{k}: {v}" for k, v in results.items()))
            else:
                st.toast("Nothing to retry for this fund.")
            st.rerun()


def render_per_fund_status() -> None:
    """Per-fund NAV and holdings status tables on the Maintenance tab."""
    tracked = list_tracked()
    if tracked.height == 0:
        return
    with st.expander("Per-fund NAV and holdings status"):
        fr = _fund_freshness()
        names = fr["names"]
        short_by_name = get_short_names(tuple(names))
        nav_report, holdings_report = fr["nav"], fr["holdings"]
        _status_table(
            title="NAV",
            note=f"{nav_report.stale_count} of {nav_report.total} funds are behind the freshness threshold.",
            rows=build_nav_status_rows(nav_report, nav_record_stats(names), short_by_name),
        )
        _status_table(
            title="Holdings",
            note=f"{holdings_report.stale_count} of {holdings_report.total} funds are behind the freshness threshold.",
            rows=build_holdings_status_rows(holdings_report, holdings_count_by_scheme(names), short_by_name),
        )


def _status_table(*, title: str, note: str, rows: list[dict]) -> None:
    st.markdown(f"**{title}**")
    st.caption(note)
    st.dataframe(
        pd.DataFrame(rows).style.map(_color_status, subset=["Status"]),
        use_container_width=True,
        hide_index=True,
    )
