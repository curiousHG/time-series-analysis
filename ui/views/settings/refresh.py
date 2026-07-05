"""Settings → Refresh tracked-fund data section.

Refresh runs on a background task (stale-while-revalidate): the view keeps showing the last-computed
freshness + status tables while NAV/holdings update off-thread, shows live progress via a polling
fragment, and swaps to fresh data the moment the task finishes. Orchestration lives in
services.sync_service / data_freshness / registry_service."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from data.repositories.holdings import load_holdings
from data.repositories.nav import load_nav_df
from mutual_funds.display import make_slug
from services.data_freshness import (
    build_holdings_status_rows,
    build_nav_status_rows,
    compute_holdings_freshness,
    compute_nav_freshness,
)
from services.registry_service import (
    list_tracked,
    list_unavailable_funds,
    retry_unavailable,
)
from services.sync_service import refresh_all_fund_data
from ui.components.background_refresh import BackgroundRefresh, progress_cb
from ui.components.freshness_banner import clear_freshness_cache
from ui.constants import STATUS_STYLES
from ui.state.loaders import get_short_names, load_holdings_data, load_nav_data


def _color_status(val: str) -> str:
    return STATUS_STYLES.get(val, "")


@st.cache_data(ttl=300, show_spinner="Computing fund freshness…")
def _fund_freshness() -> dict:
    """Freshness reports for every tracked fund — cached so the view keeps showing the last-known
    state while a background refresh runs (cleared when the refresh finishes)."""
    names = list_tracked()["schemeName"].to_list()
    slugs = [make_slug(n) for n in names]
    return {
        "names": names,
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


def render() -> None:
    st.markdown("#### Tracked Fund Data")

    _REFRESH.consume()  # swap-to-fresh + toast if a run just finished

    tracked = list_tracked()
    if tracked.height == 0:
        st.info("No tracked funds yet. Add some via the **MF Screener** page.")
        return

    fr = _fund_freshness()
    names, slugs = fr["names"], fr["slugs"]
    nav_report, holdings_report = fr["nav"], fr["holdings"]
    short_by_name = get_short_names(tuple(names))

    if _REFRESH.is_running():
        _REFRESH.poll()  # live phase/progress banner; full-rerun when the task finishes

    h1, h2, h3, h4 = st.columns(4)
    h1.metric("Tracked funds", f"{len(names):,}")
    h2.metric("Stale NAV", f"{nav_report.stale_count:,}", help=f"Current date: {nav_report.current_date}")
    h3.metric("Stale holdings", f"{holdings_report.stale_count:,}")
    h4.metric("Unresolved sources", f"{list_unavailable_funds().height:,}")

    def _run(scope: str):
        return lambda: refresh_all_fund_data(names, scope=scope, progress_cb=progress_cb(_REFRESH.key))

    col1, col2, col3 = st.columns(3)
    with col1:
        _REFRESH.start_button("Update All NAV", _run("nav"), meta={"scope": "nav"}, type="primary")
    with col2:
        _REFRESH.start_button("Update All Holdings", _run("holdings"), meta={"scope": "holdings"})
    with col3:
        _REFRESH.start_button("Update Everything", _run("all"), meta={"scope": "all"})

    _render_retry_unavailable(short_by_name)

    with st.expander("Status details", expanded=False):
        nav_df = load_nav_df(names)
        holdings_df = load_holdings(slugs)
        _render_status_table(
            title="NAV",
            stale=f"{nav_report.stale_count} of {nav_report.total} tracked funds are stale.",
            rows=build_nav_status_rows(nav_report, nav_df, short_by_name),
        )
        _render_status_table(
            title="Holdings",
            stale=f"{holdings_report.stale_count} of {holdings_report.total} tracked funds are stale.",
            rows=build_holdings_status_rows(holdings_report, holdings_df, short_by_name),
        )


# ---- Status tables -----------------------------------------------------------------------


def _render_status_table(*, title: str, stale: str, rows: list[dict]) -> None:
    st.markdown(f"**{title}**")
    st.caption(stale)
    st.dataframe(
        pd.DataFrame(rows).style.map(_color_status, subset=["Status"]),
        use_container_width=True,
        hide_index=True,
    )


# ---- Retry-unavailable picker ------------------------------------------------------------


def _render_retry_unavailable(short_by_name: dict[str, str]) -> None:
    unavailable = list_unavailable_funds()
    if unavailable.height == 0:
        return
    st.markdown("**Retry unavailable sources**")
    sel = st.selectbox(
        "Fund",
        options=unavailable["schemeName"].to_list(),
        format_func=lambda n: short_by_name.get(n, n),
        key="retry_pick",
    )
    if st.button("Retry"):
        with st.spinner(f"Retrying {short_by_name.get(sel, sel)}…"):
            results = retry_unavailable(sel)
        if results:
            st.success(" · ".join(f"{k}: {v}" for k, v in results.items()))
        else:
            st.info("Nothing to retry — all sources already available or pending.")
        st.rerun()
