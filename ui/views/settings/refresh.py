"""Settings → Refresh tracked-fund data section.

Pure rendering. All orchestration lives in `services.sync_service` (parallel fetch +
save), `services.data_freshness` (status-table data shaping), and
`services.registry_service` (registry queries + retry).
"""

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
from services.sync_service import (
    FetchEvent,
    refresh_holdings_for_schemes,
    update_nav_incremental,
)
from ui.components.freshness_banner import clear_freshness_cache
from ui.state.loaders import get_short_names, load_holdings_data, load_nav_data

# Colour palette for the Status cell — mirrors the badges used elsewhere in the app.
_STATUS_STYLES = {
    "Fresh": "background-color: #86efac; color: #14532d",
    "Stale": "background-color: #fde68a; color: #78350f",
    "Missing": "background-color: #fca5a5; color: #7f1d1d",
    "Available": "background-color: #86efac; color: #14532d",
    "Pending": "background-color: #fde68a; color: #78350f",
    "Unavailable": "background-color: #fca5a5; color: #7f1d1d",
}


def _color_status(val: str) -> str:
    return _STATUS_STYLES.get(val, "")


def render() -> None:
    st.markdown("#### Tracked Fund Data")

    tracked = list_tracked()
    if tracked.height == 0:
        st.info("No tracked funds yet. Add some via the **MF Screener** page.")
        return

    scheme_names = tracked["schemeName"].to_list()
    short_by_name = get_short_names(tuple(scheme_names))

    with st.spinner(f"Computing freshness for {len(scheme_names):,} tracked fund(s)…"):
        slugs = [make_slug(n) for n in scheme_names]
        nav_report = compute_nav_freshness(scheme_names, slugs)
        holdings_report = compute_holdings_freshness(scheme_names, slugs)

    with st.spinner("Loading NAV and holdings data…"):
        nav_df = load_nav_df(scheme_names)
        holdings_df = load_holdings(slugs)

    h1, h2, h3, h4 = st.columns(4)
    h1.metric("Tracked funds", f"{len(scheme_names):,}")
    h2.metric("Stale NAV", f"{nav_report.stale_count:,}", help=f"Current date: {nav_report.current_date}")
    h3.metric("Stale holdings", f"{holdings_report.stale_count:,}")
    h4.metric("Unresolved sources", f"{list_unavailable_funds().height:,}")

    col1, col2, col3 = st.columns(3)
    update_nav = col1.button("Update All NAV", type="primary", use_container_width=True)
    update_holdings = col2.button("Update All Holdings", type="secondary", use_container_width=True)
    update_all = col3.button("Update Everything", type="secondary", use_container_width=True)

    if update_nav or update_all:
        _run_nav_update(scheme_names, short_by_name)
    if update_holdings or update_all:
        _run_holdings_update(scheme_names, short_by_name)

    _render_retry_unavailable(short_by_name)

    with st.expander("Status details", expanded=False):
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


# ---- Update buttons (UI shell over the sync service) -------------------------------------


def _outcome_glyph(outcome: str) -> str:
    return {"updated": "✓", "skipped": "•", "failed": "✗"}.get(outcome, "?")


def _make_progress_renderer(progress, counter_slot, log_slot, short_by_name: dict[str, str], counter_fmt):
    """Build a callback that updates the Streamlit progress bar + counters + log slot.
    `counter_fmt(counters)` shapes the markdown line; `counters` carries running totals."""
    recent: list[str] = []
    counters = {"updated": 0, "skipped": 0, "failed": 0, "new_rows": 0, "holdings_rows": 0}

    def cb(event: FetchEvent) -> None:
        short = short_by_name.get(event.scheme_name, event.scheme_name)
        progress.progress(event.done / event.total, text=f"[{event.done}/{event.total}] {short}")
        counters[event.outcome] += 1
        if event.outcome == "updated":
            # Detail starts with a number for both NAV ("<n> new rows …") and holdings
            # ("<n> holdings · …"). Parse the leading int and accumulate.
            try:
                n = int(event.detail.split(" ", 1)[0])
            except (ValueError, IndexError):
                n = 0
            # NAV pages display this as new_rows; holdings as holdings_rows. Both keys are
            # always populated; the formatter picks whichever it cares about.
            counters["new_rows"] += n
            counters["holdings_rows"] += n
        recent.append(f"{_outcome_glyph(event.outcome)} {short} — {event.detail}")
        counter_slot.markdown(counter_fmt(counters))
        log_slot.code("\n".join(recent[-8:]), language=None)

    return cb


def _run_nav_update(scheme_names: list[str], short_by_name: dict[str, str]) -> None:
    st.markdown("#### NAV Update Progress")
    total = len(scheme_names)
    progress = st.progress(0.0, text=f"Starting NAV updates for {total} fund(s)…")
    counter_slot = st.empty()
    log_slot = st.empty()

    def fmt(c: dict[str, int]) -> str:
        return (
            f"**Updated:** {c['updated']} · **Skipped:** {c['skipped']} · "
            f"**Failed:** {c['failed']} · **New rows:** {c['new_rows']:,}"
        )

    cb = _make_progress_renderer(progress, counter_slot, log_slot, short_by_name, fmt)
    result = update_nav_incremental(scheme_names, progress_cb=cb)

    progress.progress(
        1.0,
        text=f"Done — {result.updated_count} updated, {result.skipped_count} skipped, {len(result.failures)} failed",
    )
    counter_slot.markdown(
        f"**Updated:** {result.updated_count} · **Skipped:** {result.skipped_count} · "
        f"**Failed:** {len(result.failures)} · **New rows:** {result.new_rows_total:,}"
    )

    load_nav_data.clear()
    clear_freshness_cache()
    if result.failures:
        with st.expander(f"{len(result.failures)} failure(s)"):
            for name, err in result.failures:
                st.error(f"**{short_by_name.get(name, name)}** — {err}")
    st.toast(f"NAV: {result.updated_count} updated ({result.new_rows_total} new rows), {result.skipped_count} skipped")
    st.rerun()


def _run_holdings_update(scheme_names: list[str], short_by_name: dict[str, str]) -> None:
    st.markdown("#### Holdings Update Progress")
    total = len(scheme_names)
    progress = st.progress(0.0, text=f"Starting holdings updates for {total} fund(s)…")
    counter_slot = st.empty()
    log_slot = st.empty()

    def fmt(c: dict[str, int]) -> str:
        # NAV keys are reused; for holdings we ignore "skipped" / "rows".
        return (
            f"**Updated:** {c['updated']} · **Failed:** {c['failed']} · "
            f"**Holdings rows so far:** {c.get('holdings_rows', 0):,}"
        )

    cb = _make_progress_renderer(progress, counter_slot, log_slot, short_by_name, fmt)

    result = refresh_holdings_for_schemes(scheme_names, progress_cb=cb)

    progress.progress(
        1.0,
        text=f"Done — {result.success_count} updated, {len(result.failures)} failed",
    )
    counter_slot.markdown(
        f"**Updated:** {result.success_count} · **Failed:** {len(result.failures)} · "
        f"**Holdings rows:** {result.total_holdings:,}"
    )

    load_holdings_data.clear()
    clear_freshness_cache()
    if result.failures:
        with st.expander(f"{len(result.failures)} failure(s)"):
            for name, err in result.failures:
                st.error(f"**{short_by_name.get(name, name)}** — {err}")
    st.toast(f"Saved holdings for {result.success_count}/{total} funds")
    st.rerun()


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
