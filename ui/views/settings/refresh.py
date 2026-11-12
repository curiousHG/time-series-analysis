"""Settings → Funds: freshness dates, the two routine refresh jobs and the unresolved-source picker.

Refresh runs as a background task; per-fund status tables live on the Maintenance tab.
"""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass

import pandas as pd
import polars as pl
import streamlit as st

from core.enums import SourceStatus, TaskStatus
from mutual_funds.display import make_slug
from services.constants import RefreshScope
from services.mf.backfill import retry_unavailable
from services.mf.freshness import (
    FreshnessReport,
    build_holdings_status_rows,
    build_nav_status_rows,
    business_days_between,
    compute_holdings_freshness,
    compute_nav_freshness,
)
from services.mf.registry import (
    list_active,
    list_tracked,
    list_unavailable_funds,
)
from services.mf.sync import FundRefreshSummary, refresh_all_fund_data
from ui.components.background_refresh import BackgroundRefresh, progress_cb
from ui.components.chrome import section_header
from ui.components.freshness_banner import clear_freshness_cache
from ui.constants import STATUS_STYLES
from ui.state.loaders import (
    clear_fund_data_caches,
    get_short_names,
    load_amfi_status_cached,
    load_holdings_counts_cached,
    load_nav_record_stats_cached,
)
from ui.views.settings.format import fmt_date, plural


def _color_status(val: str) -> str:
    return STATUS_STYLES.get(val, "")


@dataclass(frozen=True, slots=True)
class FundFreshness:
    """Tracked funds with their NAV and holdings freshness reports."""

    names: list[str]
    nav_names: list[str]
    name_to_code: dict[str, int]
    slugs: list[str]
    nav: FreshnessReport
    holdings: FreshnessReport


@st.cache_data(ttl=300, show_spinner="Computing fund freshness…")
def _fund_freshness() -> FundFreshness:
    """Freshness reports for every tracked fund — cached so the view keeps showing the last-known
    state while a background refresh runs (cleared when the refresh finishes)."""
    active = list_active()
    names = active["schemeName"].to_list()
    name_to_code = dict(zip(names, active["schemeCode"].to_list(), strict=True))
    slugs = [make_slug(n) for n in names]
    return FundFreshness(
        names=names,
        nav_names=active.filter(pl.col("navStatus") != SourceStatus.UNAVAILABLE)["schemeName"].to_list(),
        name_to_code=name_to_code,
        slugs=slugs,
        nav=compute_nav_freshness(names, slugs),
        holdings=compute_holdings_freshness(names, slugs),
    )


def _clear_fund_caches() -> None:
    _fund_freshness.clear()
    clear_fund_data_caches()
    clear_freshness_cache()


def _fund_summary(r: object) -> str:
    parts = []
    if isinstance(r, FundRefreshSummary):
        if r.nav is not None:
            parts.append(f"NAV {r.nav.updated_count:,} updated (+{r.nav.new_rows_total:,} rows)")
        if r.holdings is not None:
            parts.append(f"holdings {r.holdings.success_count:,} updated")
        failed = (len(r.nav.failures) if r.nav else 0) + (len(r.holdings.failures) if r.holdings else 0)
        if failed:
            parts.append(f"{failed:,} failed, see Last refresh")
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
    amfi = load_amfi_status_cached().latest_nav_date
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
    names = fr.names
    dormant = int(tracked["dormant"].sum())

    def _run(scope: RefreshScope):
        scoped = fr.nav_names if scope == RefreshScope.NAV else names
        return lambda: refresh_all_fund_data(
            scoped, name_to_code=fr.name_to_code, scope=scope, progress_cb=progress_cb(_REFRESH.key)
        )

    with actions:
        b1, b2 = st.columns(2)
        with b1:
            _REFRESH.start_button(
                "Update NAV", _run(RefreshScope.NAV), meta={"scope": RefreshScope.NAV}, type="primary"
            )
        with b2:
            _REFRESH.start_button("Update holdings", _run(RefreshScope.HOLDINGS), meta={"scope": RefreshScope.HOLDINGS})
    if _REFRESH.is_running():
        _REFRESH.poll()
    else:
        _render_last_run()

    st.markdown(_nav_line(fr.nav))
    holdings_latest = _latest(fr.holdings)
    if holdings_latest is None:
        st.markdown("No holdings stored yet.")
    else:
        st.markdown(f"Holdings as of {fmt_date(holdings_latest)}, the latest portfolio AdvisorKhoj has published.")
    st.caption(f"{plural(len(names), 'active fund')} tracked, {plural(dormant, 'dormant scheme')} skipped.")
    _render_unresolved()


def _phase_lines(result: FundRefreshSummary) -> list[tuple[str, str, list[tuple[str, str]]]]:
    """(phase, one-line outcome, failures) for each phase the run covered."""
    lines = []
    if result.nav is not None:
        nav = result.nav
        lines.append(
            (
                "NAV",
                f"{plural(nav.updated_count, 'fund')} updated (+{nav.new_rows_total:,} rows), "
                f"{nav.skipped_count:,} already current, {len(nav.failures):,} failed.",
                nav.failures,
            )
        )
    if result.holdings is not None:
        holdings = result.holdings
        lines.append(
            (
                "Holdings",
                f"{plural(holdings.success_count, 'fund')} updated ({holdings.total_holdings:,} rows), "
                f"{len(holdings.failures):,} failed.",
                holdings.failures,
            )
        )
    return lines


def _render_last_run() -> None:
    """What the previous refresh did, kept on screen until the next one finishes: counts per phase
    and every failure, grouped by reason."""
    last = _REFRESH.last()
    if last is None or last.finished_at is None:
        return
    finished = time.strftime("%H:%M", time.localtime(last.finished_at))
    took = round(last.finished_at - (last.started_at or last.finished_at))
    with st.container(border=True):
        st.markdown(f"**Last refresh** · finished {finished}, took {took // 60}m {took % 60:02d}s")
        if last.status == TaskStatus.FAILED:
            st.error(f"The refresh stopped with an error: {last.error}")
            return
        for phase, line, failures in _phase_lines(
            last.result if isinstance(last.result, FundRefreshSummary) else FundRefreshSummary()
        ):
            st.markdown(f"{phase}: {line}")
            if failures:
                _render_failures(phase, failures)


def _render_failures(phase: str, failures: list[tuple[str, str]]) -> None:
    by_reason = Counter(reason for _, reason in failures)
    st.dataframe(
        pd.DataFrame(by_reason.most_common(), columns=["Reason", "Funds"]),
        use_container_width=True,
        hide_index=True,
    )
    short_by_name = get_short_names(tuple(name for name, _ in failures))
    with st.expander(f"{plural(len(failures), f'{phase} failure')} by fund"):
        st.dataframe(
            pd.DataFrame(
                sorted((short_by_name.get(name, name), reason) for name, reason in failures),
                columns=["Fund", "Reason"],
            ),
            use_container_width=True,
            hide_index=True,
        )


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
            clear_fund_data_caches()
            clear_freshness_cache()
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
        names = fr.names
        short_by_name = get_short_names(tuple(names))
        nav_report, holdings_report = fr.nav, fr.holdings
        _status_table(
            title="NAV",
            note=f"{nav_report.stale_count} of {nav_report.total} funds are behind the freshness threshold.",
            rows=build_nav_status_rows(nav_report, load_nav_record_stats_cached(tuple(names)), short_by_name),
        )
        _status_table(
            title="Holdings",
            note=f"{holdings_report.stale_count} of {holdings_report.total} funds are behind the freshness threshold.",
            rows=build_holdings_status_rows(holdings_report, load_holdings_counts_cached(tuple(names)), short_by_name),
        )


def _status_table(*, title: str, note: str, rows: list[dict]) -> None:
    st.markdown(f"**{title}**")
    st.caption(note)
    st.dataframe(
        pd.DataFrame(rows).style.map(_color_status, subset=["Status"]),
        use_container_width=True,
        hide_index=True,
    )
