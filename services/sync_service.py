"""Sync service — orchestrates parallel fetches of NAV / holdings for tracked funds.

UI calls into this layer; this layer pulls from `data.fetchers` (HTTP) and hands the
results off to `data.repositories` (DB writes). Per-fund completion events are emitted
back via an optional progress callback so the caller can drive a progress bar / log
without having to know anything about thread pools.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

import polars as pl

from data.repositories.holdings import fetch_holdings_frames, replace_holdings_atomic
from data.repositories.nav import fetch_single_nav, last_nav_date_by_name, save_nav_df
from mutual_funds.display import make_slug
from services.constants import HOLDINGS_FETCH_WORKERS, NAV_FETCH_WORKERS, FetchOutcome

logger = logging.getLogger("services.sync")


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
    progress_cb: ProgressCb | None = None,
) -> NavUpdateResult:
    """Fetch latest NAV for each name; save only rows newer than what's already in DB.

    Fetches run on a 16-worker pool (network-bound). DB saves run on the main thread to
    keep Postgres writes serialised. The callback fires once per fund as it completes.
    """
    if not scheme_names:
        return NavUpdateResult()

    last_known_by_name = last_nav_date_by_name(scheme_names)
    total = len(scheme_names)
    result = NavUpdateResult()
    done = 0

    with ThreadPoolExecutor(max_workers=NAV_FETCH_WORKERS) as pool:
        future_to_name = {pool.submit(fetch_single_nav, name): name for name in scheme_names}
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
                result.failures.append((name, str(e)))
                outcome, detail = "failed", str(e)

            _emit(progress_cb, FetchEvent(done, total, name, outcome, detail))

    return result


# ---- Holdings ----------------------------------------------------------------------------


def _fetch_normalize_holdings(slug: str) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """Pull a slug's portfolio JSON and normalise it into the three target frames."""
    return fetch_holdings_frames(slug)


def refresh_holdings_for_schemes(
    scheme_names: list[str],
    *,
    progress_cb: ProgressCb | None = None,
) -> HoldingsRefreshResult:
    """Wipe-and-refetch holdings for the given schemes.

    Fetch every fund first. For each successful fetch, delete and replace only that
    fund's rows; failed fetches leave the existing local snapshot intact.
    """
    if not scheme_names:
        return HoldingsRefreshResult()

    pairs = [(name, make_slug(name)) for name in scheme_names]

    total = len(pairs)
    result = HoldingsRefreshResult()
    done = 0

    with ThreadPoolExecutor(max_workers=HOLDINGS_FETCH_WORKERS) as pool:
        future_to_pair = {pool.submit(_fetch_normalize_holdings, slug): (name, slug) for name, slug in pairs}
        for future in as_completed(future_to_pair):
            name, _slug = future_to_pair[future]
            done += 1
            outcome: FetchOutcome
            detail: str

            try:
                h, s, a = future.result()
                replace_holdings_atomic(_slug, h, s, a)
                result.success_count += 1
                result.total_holdings += h.height
                outcome = "updated"
                detail = f"{h.height} holdings · {s.height} sectors · {a.height} asset types"
            except Exception as e:
                result.failures.append((name, str(e)))
                outcome, detail = "failed", str(e)

            _emit(progress_cb, FetchEvent(done, total, name, outcome, detail))

    return result
