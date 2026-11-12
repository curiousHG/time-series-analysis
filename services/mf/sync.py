"""Sync service — parallel NAV/holdings fetches for tracked funds.

Pulls from `data.fetchers` (HTTP) into `data.repositories` (DB). Emits per-fund completion
events via an optional progress callback so callers can drive a progress bar without knowing
about thread pools.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

import httpx
import polars as pl

from data.exceptions import SourceNotFound, SourceThrottled
from data.repositories.advisorkhoj_keys import fetch_holdings_for_scheme
from data.repositories.holdings import refresh_holdings_data, replace_holdings_atomic, slug_to_code_map
from data.repositories.nav import last_nav_date_by_code, save_nav_df
from data.repositories.scheme_codes import resolve_codes
from mutual_funds.display import make_slug, short_scheme_name
from services.constants import HOLDINGS_FETCH_WORKERS, NAV_FETCH_WORKERS, FetchOutcome, RefreshScope
from services.mf.nav_sources import fetch_single_nav, refresh_nav_data
from services.mf.registry import mark_nav_unavailable

logger = logging.getLogger(__name__)


@dataclass
class FetchEvent:
    """Per-fund completion event emitted to the progress callback."""

    done: int
    total: int
    scheme_name: str
    outcome: FetchOutcome
    detail: str


ProgressCb = Callable[[FetchEvent], None]


@dataclass
class NavUpdateResult:
    updated_count: int = 0
    skipped_count: int = 0
    new_rows_total: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class HoldingsRefreshResult:
    success_count: int = 0
    total_holdings: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class FundRefreshSummary:
    """Outcome of a fund refresh; a phase the scope did not cover is None."""

    nav: NavUpdateResult | None = None
    holdings: HoldingsRefreshResult | None = None


def _emit(progress_cb: ProgressCb | None, event: FetchEvent) -> None:
    if progress_cb is None:
        return
    try:
        progress_cb(event)
    except Exception:
        logger.exception("progress_cb raised; continuing")


def failure_reason(error: Exception, name: str) -> str:
    """The cause of a failed fetch with the fund's own name taken out, so a run's failures group
    by why they failed rather than splitting into one row per fund."""
    if isinstance(error, httpx.HTTPStatusError):
        return f"HTTP {error.response.status_code} from {error.request.url.host}"
    if isinstance(error, (SourceNotFound, SourceThrottled)):
        return str(error).replace(f" for {name!r}", "")
    return f"{type(error).__name__}: {str(error).replace(repr(name), 'the fund')}"


def _record_failure(result, kind: str, name: str, error: Exception) -> tuple[FetchOutcome, str]:
    logger.warning("%s refresh failed for %s: %s", kind, name, error)
    reason = failure_reason(error, name)
    result.failures.append((name, reason))
    return FetchOutcome.FAILED, reason


def update_nav_incremental(
    scheme_names: list[str],
    *,
    name_to_code: dict[str, int] | None = None,
    progress_cb: ProgressCb | None = None,
) -> NavUpdateResult:
    """Fetch latest NAV per name; save only rows newer than what's in DB. Callback fires once per fund."""
    if not scheme_names:
        return NavUpdateResult()

    codes = name_to_code or resolve_codes(scheme_names)
    last_by_code = last_nav_date_by_code(list(codes.values()))
    last_known_by_name = {name: last_by_code[code] for name, code in codes.items() if code in last_by_code}
    total = len(scheme_names)
    result = NavUpdateResult()
    done = 0

    with ThreadPoolExecutor(max_workers=NAV_FETCH_WORKERS) as pool:
        future_to_name = {pool.submit(fetch_single_nav, name, codes.get(name)): name for name in scheme_names}
        for future in as_completed(future_to_name):
            name = future_to_name[future]
            done += 1
            outcome: FetchOutcome
            detail: str

            try:
                df = future.result()
                last_known = last_known_by_name.get(name)
                api_max = df.select("date").to_series().max() if df.height > 0 else None

                if last_known is not None:
                    df = df.filter(pl.col("date") > last_known)

                if df.height == 0:
                    result.skipped_count += 1
                    if api_max is None:
                        outcome, detail = FetchOutcome.SKIPPED, "source returned no rows"
                    else:
                        outcome, detail = FetchOutcome.SKIPPED, f"up to date ({api_max})"
                else:
                    save_nav_df(df)
                    dates = df.select("date").to_series()
                    result.new_rows_total += df.height
                    result.updated_count += 1
                    outcome = FetchOutcome.UPDATED
                    detail = f"{df.height} new rows ({dates.min()} → {dates.max()})"
            except SourceNotFound as e:
                if name not in last_known_by_name:
                    mark_nav_unavailable(name)
                outcome, detail = _record_failure(result, "NAV", name, e)
            except Exception as e:
                outcome, detail = _record_failure(result, "NAV", name, e)

            _emit(progress_cb, FetchEvent(done, total, name, outcome, detail))

    return result


def refresh_holdings_for_schemes(
    scheme_names: list[str],
    *,
    progress_cb: ProgressCb | None = None,
) -> HoldingsRefreshResult:
    """Wipe-and-refetch holdings per scheme. Each success replaces only its own rows;
    failed fetches leave the existing local snapshot intact.
    """
    if not scheme_names:
        return HoldingsRefreshResult()

    codes = slug_to_code_map()
    pairs = [(name, make_slug(name)) for name in scheme_names]
    pairs = [(name, slug) for name, slug in pairs if slug in codes]

    total = len(pairs)
    result = HoldingsRefreshResult()
    done = 0

    with ThreadPoolExecutor(max_workers=HOLDINGS_FETCH_WORKERS) as pool:
        future_to_pair = {pool.submit(fetch_holdings_for_scheme, codes[slug]): (name, slug) for name, slug in pairs}
        for future in as_completed(future_to_pair):
            name, _slug = future_to_pair[future]
            done += 1
            outcome: FetchOutcome
            detail: str

            try:
                h, s, a = future.result()
                replace_holdings_atomic(_slug, h, s, a, scheme_code=codes[_slug])
                result.success_count += 1
                result.total_holdings += h.height
                outcome = FetchOutcome.UPDATED
                detail = f"{h.height} holdings · {s.height} sectors · {a.height} asset types"
            except Exception as e:
                outcome, detail = _record_failure(result, "holdings", name, e)

            _emit(progress_cb, FetchEvent(done, total, name, outcome, detail))

    return result


def refresh_all_fund_data(
    scheme_names: list[str],
    *,
    name_to_code: dict[str, int] | None = None,
    scope: RefreshScope = RefreshScope.ALL,
    progress_cb: Callable[..., None] | None = None,
) -> FundRefreshSummary:
    """Run NAV and/or holdings updates for a set of tracked funds, suitable for a background task.

    `scope` is 'nav', 'holdings', or 'all'. `progress_cb(**fields)` receives live phase/done/total
    (e.g. phase='NAV', done=12, total=99) so a polling UI can show progress. Returns a summary holding
    each covered phase's result, whose `failures` are (scheme name, reason) pairs.
    """
    nav: NavUpdateResult | None = None
    holdings: HoldingsRefreshResult | None = None
    phases = [
        label
        for label, phase_scope in (("NAV", RefreshScope.NAV), ("Holdings", RefreshScope.HOLDINGS))
        if scope in (RefreshScope.ALL, phase_scope)
    ]
    if progress_cb:
        progress_cb(
            message=f"Starting {scope} refresh for {len(scheme_names):,} tracked fund(s) — {' + '.join(phases)}."
        )

    def _log_event(phase: str, ev: FetchEvent) -> None:
        """Forward one fund's outcome to the UI log; up-to-date (skipped) funds are not logged."""
        if progress_cb is None or ev.outcome == FetchOutcome.SKIPPED:
            return
        icon = "✓" if ev.outcome == FetchOutcome.UPDATED else "✗"
        progress_cb(message=f"{icon} [{phase} {ev.done}/{ev.total}] {short_scheme_name(ev.scheme_name)} — {ev.detail}")

    if scope in (RefreshScope.ALL, RefreshScope.NAV):

        def _nav_cb(ev: FetchEvent) -> None:
            if progress_cb:
                progress_cb(phase="NAV", done=ev.done, total=ev.total)
            _log_event("NAV", ev)

        nav = update_nav_incremental(scheme_names, name_to_code=name_to_code, progress_cb=_nav_cb)
        if progress_cb:
            progress_cb(
                message=f"NAV done — {nav.updated_count:,} updated (+{nav.new_rows_total:,} rows), "
                f"{nav.skipped_count:,} already current, {len(nav.failures):,} failed."
            )

    if scope in (RefreshScope.ALL, RefreshScope.HOLDINGS):

        def _hold_cb(ev: FetchEvent) -> None:
            if progress_cb:
                progress_cb(phase="Holdings", done=ev.done, total=ev.total)
            _log_event("Holdings", ev)

        holdings = refresh_holdings_for_schemes(scheme_names, progress_cb=_hold_cb)
        if progress_cb:
            progress_cb(
                message=f"Holdings done — {holdings.success_count:,} updated "
                f"({holdings.total_holdings:,} rows), {len(holdings.failures):,} failed."
            )

    counts: dict[str, int] = {}
    if nav is not None:
        counts |= {
            "nav_updated": nav.updated_count,
            "nav_skipped": nav.skipped_count,
            "nav_new_rows": nav.new_rows_total,
            "nav_failed": len(nav.failures),
        }
    if holdings is not None:
        counts |= {
            "holdings_updated": holdings.success_count,
            "holdings_rows": holdings.total_holdings,
            "holdings_failed": len(holdings.failures),
        }
    logger.info("refresh_all_fund_data(scope=%s): %s", scope, counts)
    return FundRefreshSummary(nav=nav, holdings=holdings)


def refresh_funds(
    names: list[str],
    slugs: list[str],
    *,
    include_metadata: bool = False,
    progress_cb: Callable[..., None] | None = None,
) -> list[str]:
    """Refetch NAV, holdings, optionally metadata, then recompute metrics for `names`. A failing step is
    recorded, not fatal; returns the labels of the steps that raised."""
    from data.repositories.metadata import refresh_metadata  # noqa: PLC0415
    from services.mf.metrics import recompute_metrics  # noqa: PLC0415

    def _refresh_all_metadata() -> None:
        for name in names:
            refresh_metadata(name)

    steps = [
        ("NAV", lambda: refresh_nav_data(names)),
        ("Holdings", lambda: refresh_holdings_data(slugs)),
    ]
    if include_metadata:
        steps.append(("Metadata", _refresh_all_metadata))
    steps.append(("Metrics", lambda: recompute_metrics(names)))

    failed: list[str] = []
    for i, (label, step) in enumerate(steps):
        if progress_cb is not None:
            progress_cb(phase=label, done=i, total=len(steps))
        try:
            step()
        except Exception:
            logger.warning("refresh_funds: %s step failed", label, exc_info=True)
            failed.append(label)
    if progress_cb is not None:
        progress_cb(phase="Done", done=len(steps), total=len(steps))
    return failed
