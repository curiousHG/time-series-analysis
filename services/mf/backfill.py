"""Backfill — fetch missing NAV, holdings and metadata for tracked funds."""

from __future__ import annotations

import contextlib
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING

from core.database import get_session
from core.models import MfRegistry
from data.repositories.advisorkhoj_keys import fetch_holdings_for_scheme
from data.repositories.holdings import save_assets, save_holdings, save_sectors
from data.repositories.metadata import fetch_and_save as fetch_metadata_and_save
from data.repositories.nav import save_nav_df
from services.mf.nav_sources import fetch_single_nav
from services.mf.registry import list_tracked, resolve_scheme_code, set_status, upsert_registry

if TYPE_CHECKING:
    from collections.abc import Callable

    from services.constants import BackfillSource, SourceStatus

logger = logging.getLogger(__name__)


def _fetch_nav(scheme_name: str) -> SourceStatus:
    try:
        df = fetch_single_nav(scheme_name)
        if df.height == 0:
            return "unavailable"
        save_nav_df(df)
        return "available"
    except Exception as e:
        logger.warning("NAV fetch failed for %s: %s", scheme_name, e)
        return "unavailable"


def _fetch_holdings(scheme_name: str) -> SourceStatus:
    code = resolve_scheme_code(scheme_name)
    if code is None:
        return "unavailable"
    try:
        h, s, a = fetch_holdings_for_scheme(code)
        if h.height == 0 and s.height == 0 and a.height == 0:
            return "unavailable"
        save_holdings(h, scheme_code=code)
        save_sectors(s, scheme_code=code)
        save_assets(a, scheme_code=code)
        return "available"
    except Exception as e:
        logger.warning("Holdings fetch failed for %s: %s", scheme_name, e)
        return "unavailable"


def _fetch_metadata(scheme_name: str) -> SourceStatus:
    try:
        meta = fetch_metadata_and_save(scheme_name)
        if not meta or not any(meta.get(k) for k in ("aum_crores", "expense_ratio", "benchmark", "launch_date")):
            return "unavailable"
        return "available"
    except Exception as e:
        logger.warning("Metadata fetch failed for %s: %s", scheme_name, e)
        return "unavailable"


def retry_unavailable(scheme_name: str) -> dict[str, SourceStatus]:
    """Retry only the sources currently marked 'unavailable' for a fund."""
    code = resolve_scheme_code(scheme_name)
    if code is None:
        return {}
    with get_session() as session:
        row = session.get(MfRegistry, code)
        if row is None:
            return {}
        targets = {
            "nav_status": row.nav_status,
            "holdings_status": row.holdings_status,
            "metadata_status": row.metadata_status,
        }

    results: dict[str, SourceStatus] = {}
    if targets["nav_status"] == "unavailable":
        results["nav_status"] = _fetch_nav(scheme_name)
    if targets["holdings_status"] == "unavailable":
        results["holdings_status"] = _fetch_holdings(scheme_name)
    if targets["metadata_status"] == "unavailable":
        results["metadata_status"] = _fetch_metadata(scheme_name)

    if results:
        set_status(scheme_name, **results)
    return results


def backfill_missing(
    *,
    scheme_names: list[str] | None = None,
    sources: tuple[BackfillSource, ...] = ("nav", "metadata"),
    max_per_run: int = 50,
    submit_delay: float = 0.05,
    max_workers: int = 8,
    progress_cb: Callable[[int, int, str, str], None] | None = None,
) -> dict[str, list[str]]:
    """Fetch missing nav/metadata/holdings for tracked funds, rate limited; returns {fetched, failed, skipped}."""
    if scheme_names is not None:
        for name in scheme_names:
            upsert_registry(name)

    tracked = list_tracked()
    if tracked.is_empty():
        return {"fetched": [], "failed": [], "skipped": []}

    rows_by_name = {row["schemeName"]: row for row in tracked.iter_rows(named=True)}

    if scheme_names is not None:
        ordered_rows = [rows_by_name[n] for n in scheme_names if n in rows_by_name]
        retry_unavailable_too = True
    else:
        ordered_rows = list(tracked.iter_rows(named=True))
        retry_unavailable_too = False

    def _needs(status: str) -> bool:
        if status == "available":
            return False
        if status == "pending":
            return True
        return retry_unavailable_too

    todo: list[tuple[str, str]] = []
    for row in ordered_rows:
        name = row["schemeName"]
        if row["dormant"]:
            continue
        if "nav" in sources and _needs(row["navStatus"]):
            todo.append((name, "nav"))
        if "metadata" in sources and _needs(row["metadataStatus"]):
            todo.append((name, "metadata"))
        if "holdings" in sources and _needs(row["holdingsStatus"]):
            todo.append((name, "holdings"))
        if len(todo) >= max_per_run:
            break
    todo = todo[:max_per_run]

    if not todo:
        return {"fetched": [], "failed": [], "skipped": []}

    def _do_one(scheme_name: str, source: str) -> tuple[str, str, str]:
        try:
            if source == "nav":
                status = _fetch_nav(scheme_name)
                set_status(scheme_name, nav_status=status)
            elif source == "metadata":
                status = _fetch_metadata(scheme_name)
                set_status(scheme_name, metadata_status=status)
            elif source == "holdings":
                status = _fetch_holdings(scheme_name)
                set_status(scheme_name, holdings_status=status)
            else:
                status = "unavailable"
        except Exception as e:
            logger.warning("backfill failed for %s/%s: %s", scheme_name, source, e)
            status = "unavailable"
        return scheme_name, source, status

    fetched: list[str] = []
    failed: list[str] = []
    futures: list = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for name, source in todo:
            futures.append(pool.submit(_do_one, name, source))
            time.sleep(submit_delay)

        for done, fut in enumerate(as_completed(futures), start=1):
            name, source, status = fut.result()
            if progress_cb is not None:
                with contextlib.suppress(Exception):
                    progress_cb(done, len(todo), name, source)
            tag = f"{source}: {name}"
            if status == "available":
                fetched.append(tag)
            else:
                failed.append(tag)

    logger.info("Backfill done — fetched %d, failed %d", len(fetched), len(failed))
    return {"fetched": fetched, "failed": failed, "skipped": []}
