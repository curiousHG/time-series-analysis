"""Settings → Refresh tracked-fund data section.

Refresh runs on a background task (stale-while-revalidate): the view keeps showing the last-computed
freshness + status tables while NAV/holdings update off-thread, shows live progress via a polling
fragment, and swaps to fresh data the moment the task finishes. Orchestration lives in
services.sync_service / data_freshness / registry_service."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from core.background import consume_if_finished, is_running, set_task_progress, start_task, task_state
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
from ui.components.freshness_banner import clear_freshness_cache
from ui.constants import STATUS_STYLES
from ui.state.loaders import get_short_names, load_holdings_data, load_nav_data

_FUND_KEY = "fund_data_refresh"


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


def _start_refresh(scope: str, names: list[str]) -> None:
    start_task(
        _FUND_KEY,
        lambda: refresh_all_fund_data(names, scope=scope, progress_cb=lambda **f: set_task_progress(_FUND_KEY, **f)),
        meta={"scope": scope},
    )


def render() -> None:
    st.markdown("#### Tracked Fund Data")

    # Consume a just-finished background refresh (swap to fresh data + toast the outcome).
    finished = consume_if_finished(_FUND_KEY)
    if finished is not None:
        _clear_fund_caches()
        if finished.status == "done":
            r = finished.result or {}
            parts = []
            if "nav_updated" in r:
                parts.append(f"NAV {r['nav_updated']} updated (+{r.get('nav_new_rows', 0):,} rows)")
            if "holdings_updated" in r:
                parts.append(f"holdings {r['holdings_updated']} updated")
            st.toast("Fund refresh done — " + (", ".join(parts) if parts else "no changes") + ".", icon="✅")
        else:
            st.toast(f"Fund refresh failed: {finished.error}", icon="⚠️")

    tracked = list_tracked()
    if tracked.height == 0:
        st.info("No tracked funds yet. Add some via the **MF Screener** page.")
        return

    fr = _fund_freshness()
    names, slugs = fr["names"], fr["slugs"]
    nav_report, holdings_report = fr["nav"], fr["holdings"]
    short_by_name = get_short_names(tuple(names))

    refreshing = is_running(_FUND_KEY)
    if refreshing:
        _poll_fund_refresh()  # live progress banner; triggers a full rerun when the task finishes

    h1, h2, h3, h4 = st.columns(4)
    h1.metric("Tracked funds", f"{len(names):,}")
    h2.metric("Stale NAV", f"{nav_report.stale_count:,}", help=f"Current date: {nav_report.current_date}")
    h3.metric("Stale holdings", f"{holdings_report.stale_count:,}")
    h4.metric("Unresolved sources", f"{list_unavailable_funds().height:,}")

    col1, col2, col3 = st.columns(3)
    if col1.button("Update All NAV", type="primary", disabled=refreshing, use_container_width=True):
        _start_refresh("nav", names)
        st.rerun()
    if col2.button("Update All Holdings", disabled=refreshing, use_container_width=True):
        _start_refresh("holdings", names)
        st.rerun()
    if col3.button("Update Everything", disabled=refreshing, use_container_width=True):
        _start_refresh("all", names)
        st.rerun()

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


@st.fragment(run_every="3s")
def _poll_fund_refresh() -> None:
    """Poll the background refresh: show live phase/progress, and full-rerun when it finishes."""
    if not is_running(_FUND_KEY):
        st.rerun(scope="app")  # finished → rerun the page to consume the result + show fresh data
        return
    meta = task_state(_FUND_KEY).meta
    phase = meta.get("phase", "…")
    done, total = meta.get("done", 0), meta.get("total", 0) or 1
    st.info(f"🔄 Refreshing in the background — {phase} [{done}/{total}]. Showing last-cached freshness meanwhile.")
    st.progress(min(done / total, 1.0))


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
