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

import polars as pl

from data.repositories.holdings import fetch_holdings_for_scheme, replace_holdings_atomic, slug_to_code_map
from data.repositories.nav import codes_for_names, fetch_single_nav, last_nav_date_by_code, save_nav_df
from mutual_funds.display import make_slug, short_scheme_name
from services.constants import HOLDINGS_FETCH_WORKERS, NAV_FETCH_WORKERS, FetchOutcome

logger = logging.getLogger(__name__)


# ---- progress / result types ------------------------------------------------------------


@dataclass
class FetchEvent:
    """Per-fund completion event emitted to the progress callback."""

    done: int  # 1-indexed completed count across the run
    total: int
    scheme_name: str
    outcome: FetchOutcome
    detail: str  # human-readable one-liner (new rows / up-to-date / error)


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


def _emit(progress_cb: ProgressCb | None, event: FetchEvent) -> None:
    if progress_cb is None:
        return
    try:
        progress_cb(event)
    except Exception:
        logger.exception("progress_cb raised; continuing")


# ---- NAV ---------------------------------------------------------------------------------


def update_nav_incremental(
    scheme_names: list[str],
    *,
    name_to_code: dict[str, int] | None = None,
    progress_cb: ProgressCb | None = None,
) -> NavUpdateResult:
    """Fetch latest NAV per name; save only rows newer than what's in DB.

    Fetches run on a worker pool (network-bound); DB saves stay on the main thread to keep
    Postgres writes serialised. Callback fires once per fund.
    """
    if not scheme_names:
        return NavUpdateResult()

    codes = name_to_code or codes_for_names(scheme_names)
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
                        outcome, detail = "skipped", "source returned no rows"
                    else:
                        outcome, detail = "skipped", f"up to date ({api_max})"
                else:
                    save_nav_df(df)  # serialised on main thread
                    dates = df.select("date").to_series()
                    result.new_rows_total += df.height
                    result.updated_count += 1
                    outcome = "updated"
                    detail = f"{df.height} new rows ({dates.min()} → {dates.max()})"
            except Exception as e:
                logger.warning("NAV refresh failed for %s: %s", name, e)
                result.failures.append((name, str(e)))
                outcome, detail = "failed", str(e)

            _emit(progress_cb, FetchEvent(done, total, name, outcome, detail))

    return result


# ---- Holdings ----------------------------------------------------------------------------


def _fetch_normalize_holdings(scheme_code: int) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """Resolve the scheme on AdvisorKhoj, pull its portfolio and normalise it into the three frames."""
    return fetch_holdings_for_scheme(scheme_code)


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
        future_to_pair = {pool.submit(_fetch_normalize_holdings, codes[slug]): (name, slug) for name, slug in pairs}
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
                outcome = "updated"
                detail = f"{h.height} holdings · {s.height} sectors · {a.height} asset types"
            except Exception as e:
                logger.warning("holdings refresh failed for %s: %s", name, e)
                result.failures.append((name, str(e)))
                outcome, detail = "failed", str(e)

            _emit(progress_cb, FetchEvent(done, total, name, outcome, detail))

    return result


# ---- Background full-refresh orchestrator ------------------------------------------------


def refresh_all_fund_data(
    scheme_names: list[str],
    *,
    name_to_code: dict[str, int] | None = None,
    scope: str = "all",
    progress_cb: Callable[..., None] | None = None,
) -> dict:
    """Run NAV and/or holdings updates for a set of tracked funds, suitable for a background task.

    `scope` is 'nav', 'holdings', or 'all'. `progress_cb(**fields)` receives live phase/done/total
    (e.g. phase='NAV', done=12, total=99) so a polling UI can show progress. Returns a summary dict.
    """
    out: dict = {}
    phases = [p for p in ("NAV", "Holdings") if scope in ("all", p.lower())]
    if progress_cb:
        progress_cb(
            message=f"Starting {scope} refresh for {len(scheme_names):,} tracked fund(s) — {' + '.join(phases)}."
        )

    def _log_event(phase: str, ev: FetchEvent) -> None:
        """Forward one fund's outcome to the UI log. Up-to-date funds are counted but not
        logged — they are the overwhelming majority of a run and would bury the real activity."""
        if progress_cb is None or ev.outcome == "skipped":
            return
        icon = "✓" if ev.outcome == "updated" else "✗"
        progress_cb(message=f"{icon} [{phase} {ev.done}/{ev.total}] {short_scheme_name(ev.scheme_name)} — {ev.detail}")

    if scope in ("all", "nav"):

        def _nav_cb(ev: FetchEvent) -> None:
            if progress_cb:
                progress_cb(phase="NAV", done=ev.done, total=ev.total)
            _log_event("NAV", ev)

        nav = update_nav_incremental(scheme_names, name_to_code=name_to_code, progress_cb=_nav_cb)
        out |= {"nav_updated": nav.updated_count, "nav_new_rows": nav.new_rows_total, "nav_failed": len(nav.failures)}
        if progress_cb:
            progress_cb(
                message=f"NAV done — {nav.updated_count:,} updated (+{nav.new_rows_total:,} rows), "
                f"{nav.skipped_count:,} already current, {len(nav.failures):,} failed."
            )

    if scope in ("all", "holdings"):

        def _hold_cb(ev: FetchEvent) -> None:
            if progress_cb:
                progress_cb(phase="Holdings", done=ev.done, total=ev.total)
            _log_event("Holdings", ev)

        holdings = refresh_holdings_for_schemes(scheme_names, progress_cb=_hold_cb)
        out |= {"holdings_updated": holdings.success_count, "holdings_failed": len(holdings.failures)}
        if progress_cb:
            progress_cb(
                message=f"Holdings done — {holdings.success_count:,} updated "
                f"({holdings.total_holdings:,} rows), {len(holdings.failures):,} failed."
            )

    logger.info("refresh_all_fund_data(scope=%s): %s", scope, out)
    return out
