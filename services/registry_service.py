"""Registry service — single source of truth for tracked funds.

Replaces the old data/repositories/registry.py. Owns the lifecycle of an mf_registry row
(insert + per-source status updates) and the auto-fetch on add.
"""

import contextlib
import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import polars as pl
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import col, delete, select

from core.database import get_session
from core.models import (
    AmfiScheme,
    MfAssetAllocation,
    MfHolding,
    MfMetadata,
    MfNav,
    MfRegistry,
    MfSectorAllocation,
)
from data.repositories.holdings import (
    load_holdings,
    save_assets,
    save_holdings,
    save_sectors,
)
from data.repositories.metadata import fetch_and_save as fetch_metadata_and_save
from data.repositories.metadata import load_metadata
from data.repositories.nav import _fetch_single_nav, _load_scheme_code_map, _save_scheme_code_map, save_nav_df
from mutual_funds.display import make_slug
from mutual_funds.holdings import (
    normalize_asset_allocation,
    normalize_holdings,
    normalize_sector_allocation,
)

logger = logging.getLogger("services.registry_service")


# ---- Read ----


def list_tracked() -> pl.DataFrame:
    """All rows from mf_registry as a polars DataFrame."""
    with get_session() as session:
        rows = session.exec(select(MfRegistry).order_by(col(MfRegistry.scheme_name))).all()
    if not rows:
        return pl.DataFrame(
            schema={
                "schemeName": pl.Utf8,
                "schemeCode": pl.Int64,
                "navStatus": pl.Utf8,
                "holdingsStatus": pl.Utf8,
                "metadataStatus": pl.Utf8,
                "addedAt": pl.Datetime,
                "lastAttemptedAt": pl.Datetime,
            }
        )
    return pl.DataFrame(
        {
            "schemeName": [r.scheme_name for r in rows],
            "schemeCode": [r.scheme_code for r in rows],
            "navStatus": [r.nav_status for r in rows],
            "holdingsStatus": [r.holdings_status for r in rows],
            "metadataStatus": [r.metadata_status for r in rows],
            "addedAt": [r.added_at for r in rows],
            "lastAttemptedAt": [r.last_attempted_at for r in rows],
        }
    )


def list_tracked_names() -> list[str]:
    with get_session() as session:
        rows = session.exec(select(MfRegistry.scheme_name).order_by(col(MfRegistry.scheme_name))).all()
    return [r for r in rows]


# ---- Status helpers ----


def _set_status(scheme_name: str, **statuses: str) -> None:
    if not statuses:
        return
    with get_session() as session:
        row = session.get(MfRegistry, scheme_name)
        if row is None:
            return
        for k, v in statuses.items():
            setattr(row, k, v)
        row.last_attempted_at = datetime.utcnow()
        session.add(row)
        session.commit()


def _upsert_registry(scheme_name: str, scheme_code: int | None) -> None:
    with get_session() as session:
        stmt = (
            pg_insert(MfRegistry)
            .values(
                scheme_name=scheme_name,
                scheme_code=scheme_code,
                nav_status="pending",
                holdings_status="pending",
                metadata_status="pending",
                added_at=datetime.utcnow(),
            )
            .on_conflict_do_update(
                index_elements=["scheme_name"],
                set_={"scheme_code": scheme_code} if scheme_code is not None else {},
            )
        )
        session.exec(stmt)
        session.commit()


def _resolve_scheme_code(scheme_name: str) -> int | None:
    with get_session() as session:
        row = session.exec(select(AmfiScheme.scheme_code).where(AmfiScheme.scheme_name == scheme_name)).first()
    return int(row) if row is not None else None


# ---- Fetchers wired to status updates ----


def _fetch_nav(scheme_name: str, code_map: dict[str, str]) -> str:
    try:
        df = _fetch_single_nav(scheme_name, code_map)
        if df.height == 0:
            return "unavailable"
        save_nav_df(df)
        return "available"
    except Exception as e:
        logger.warning("NAV fetch failed for %s: %s", scheme_name, e)
        return "unavailable"


def _fetch_holdings(scheme_name: str) -> str:
    slug = make_slug(scheme_name)
    try:
        from data.fetchers.mutual_fund import fetch_portfolio_by_slug

        resp = fetch_portfolio_by_slug(slug)
        h = normalize_holdings(resp, slug)
        s = normalize_sector_allocation(resp, slug)
        a = normalize_asset_allocation(resp, slug)
        if h.height == 0 and s.height == 0 and a.height == 0:
            return "unavailable"
        save_holdings(h)
        save_sectors(s)
        save_assets(a)
        return "available"
    except Exception as e:
        logger.warning("Holdings fetch failed for %s: %s", scheme_name, e)
        return "unavailable"


def _fetch_metadata(scheme_name: str) -> str:
    try:
        meta = fetch_metadata_and_save(scheme_name)
        if not meta or not any(meta.get(k) for k in ("aum_crores", "expense_ratio", "benchmark", "launch_date")):
            return "unavailable"
        return "available"
    except Exception as e:
        logger.warning("Metadata fetch failed for %s: %s", scheme_name, e)
        return "unavailable"


# ---- Public API ----


def add_funds(scheme_names: list[str]) -> dict[str, list[str]]:
    """For each scheme: upsert registry row, then fetch NAV+holdings+metadata in parallel.
    Statuses on the registry row are updated based on each fetch outcome.
    """
    if not scheme_names:
        return {"added": [], "partial": [], "failed": []}

    code_map = _load_scheme_code_map()
    added: list[str] = []
    partial: list[str] = []
    failed: list[str] = []

    for name in scheme_names:
        scheme_code = _resolve_scheme_code(name)
        _upsert_registry(name, scheme_code)

        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {
                pool.submit(_fetch_nav, name, code_map): "nav_status",
                pool.submit(_fetch_holdings, name): "holdings_status",
                pool.submit(_fetch_metadata, name): "metadata_status",
            }
            results: dict[str, str] = {}
            for future in as_completed(futures):
                key = futures[future]
                try:
                    results[key] = future.result()
                except Exception as e:
                    logger.error("Unexpected error in %s for %s: %s", key, name, e)
                    results[key] = "unavailable"

        _set_status(name, **results)
        statuses = list(results.values())
        if all(s == "available" for s in statuses):
            added.append(name)
        elif any(s == "available" for s in statuses):
            partial.append(name)
        else:
            failed.append(name)

    _save_scheme_code_map(code_map)
    return {"added": added, "partial": partial, "failed": failed}


def retry_unavailable(scheme_name: str) -> dict[str, str]:
    """Retry only the sources currently marked 'unavailable' for a fund."""
    with get_session() as session:
        row = session.get(MfRegistry, scheme_name)
        if row is None:
            return {}
        targets = {
            "nav_status": row.nav_status,
            "holdings_status": row.holdings_status,
            "metadata_status": row.metadata_status,
        }

    code_map = _load_scheme_code_map()
    results: dict[str, str] = {}

    if targets["nav_status"] == "unavailable":
        results["nav_status"] = _fetch_nav(scheme_name, code_map)
    if targets["holdings_status"] == "unavailable":
        results["holdings_status"] = _fetch_holdings(scheme_name)
    if targets["metadata_status"] == "unavailable":
        results["metadata_status"] = _fetch_metadata(scheme_name)

    if results:
        _set_status(scheme_name, **results)
    _save_scheme_code_map(code_map)
    return results


def backfill_missing(
    *,
    scheme_names: list[str] | None = None,
    sources: tuple[str, ...] = ("nav", "metadata"),
    max_per_run: int = 50,
    submit_delay: float = 0.4,
    max_workers: int = 2,
    progress_cb: Callable[[int, int, str, str], None] | None = None,
) -> dict[str, list[str]]:
    """Fetch missing data for tracked funds with rate limiting.

    If `scheme_names` is given, the work list is built ONLY from those names — in input order —
    and any name not already in `mf_registry` is upserted (status='pending') first, so adding
    funds from the screener filter chains naturally into a backfill. Sources whose status is
    already 'available' are skipped; 'pending' and 'unavailable' both get a fresh attempt.

    If `scheme_names` is None, falls back to the alphabetical "all tracked with pending" list.

    Args:
        scheme_names: explicit name list (e.g. top-N of a filtered screener view).
        sources: which sources to backfill (default NAV + metadata; holdings excluded — slower
            scrape, more rate-limit-sensitive).
        max_per_run: hard cap on number of fetches per call (so the UI stays responsive).
        submit_delay: seconds to sleep between submitting each work item.
        max_workers: concurrent in-flight requests.
        progress_cb: callable(done, total, scheme_name, source) called as each task completes.
    """
    if scheme_names is not None:
        # Upsert any unknown names so they appear in mf_registry with pending statuses.
        for name in scheme_names:
            scheme_code = _resolve_scheme_code(name)
            _upsert_registry(name, scheme_code)

    tracked = list_tracked()
    if tracked.is_empty():
        return {"fetched": [], "failed": [], "skipped": []}

    rows_by_name = {row["schemeName"]: row for row in tracked.iter_rows(named=True)}

    if scheme_names is not None:
        # Preserve caller-supplied order; drop any names that weren't resolvable.
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
        return retry_unavailable_too  # 'unavailable' only retried when caller explicitly listed names

    todo: list[tuple[str, str]] = []
    for row in ordered_rows:
        name = row["schemeName"]
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

    code_map = _load_scheme_code_map()

    def _do_one(scheme_name: str, source: str) -> tuple[str, str, str]:
        try:
            if source == "nav":
                status = _fetch_nav(scheme_name, code_map)
                _set_status(scheme_name, nav_status=status)
            elif source == "metadata":
                status = _fetch_metadata(scheme_name)
                _set_status(scheme_name, metadata_status=status)
            elif source == "holdings":
                status = _fetch_holdings(scheme_name)
                _set_status(scheme_name, holdings_status=status)
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
            time.sleep(submit_delay)  # throttle submission rate

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

    _save_scheme_code_map(code_map)
    logger.info("Backfill done — fetched %d, failed %d", len(fetched), len(failed))
    return {"fetched": fetched, "failed": failed, "skipped": []}


def remove_fund(scheme_name: str) -> None:
    """Drop a fund from the registry and cascade-delete its NAV/holdings/metadata rows."""
    slug = make_slug(scheme_name)
    with get_session() as session:
        session.exec(delete(MfNav).where(col(MfNav.scheme_name) == scheme_name))
        session.exec(delete(MfHolding).where(col(MfHolding.scheme_slug) == slug))
        session.exec(delete(MfSectorAllocation).where(col(MfSectorAllocation.scheme_slug) == slug))
        session.exec(delete(MfAssetAllocation).where(col(MfAssetAllocation.scheme_slug) == slug))
        session.exec(delete(MfMetadata).where(col(MfMetadata.scheme_name) == scheme_name))
        session.exec(delete(MfRegistry).where(col(MfRegistry.scheme_name) == scheme_name))
        session.commit()
    logger.info("Removed fund: %s", scheme_name)


# ---- Compatibility shim while UI is being migrated ----


def load_registry() -> pl.DataFrame:
    """Compatibility wrapper that mimics the old data/repositories/registry.load_registry shape:
    columns schemeName, schemeSlug, shortName. Used until all UI sites migrate to list_tracked().
    """
    from mutual_funds.display import short_scheme_name

    df = list_tracked()
    if df.height == 0:
        return pl.DataFrame(schema={"schemeName": pl.Utf8, "schemeSlug": pl.Utf8, "shortName": pl.Utf8})
    return df.select(
        pl.col("schemeName"),
        pl.col("schemeName").map_elements(make_slug, return_dtype=pl.Utf8).alias("schemeSlug"),
        pl.col("schemeName").map_elements(short_scheme_name, return_dtype=pl.Utf8).alias("shortName"),
    )


def save_to_registry(scheme_names: list[str]) -> None:
    """Compatibility wrapper — registers funds without auto-fetching.
    New code should call add_funds() instead."""
    if not scheme_names:
        return
    for name in scheme_names:
        scheme_code = _resolve_scheme_code(name)
        _upsert_registry(name, scheme_code)


# ---- Status backfill from data presence ----


def reconcile_statuses() -> int:
    """Set nav/holdings/metadata statuses based on actual data presence.
    Useful after manual data changes. Returns number of rows updated.
    """
    with get_session() as session:
        regs = session.exec(select(MfRegistry)).all()
        nav_names = set(session.exec(select(MfNav.scheme_name).distinct()).all())
        meta_names = set(session.exec(select(MfMetadata.scheme_name)).all())
        slugs_with_holdings = set(session.exec(select(MfHolding.scheme_slug).distinct()).all())

        updated = 0
        for r in regs:
            slug = make_slug(r.scheme_name)
            nav_s = "available" if r.scheme_name in nav_names else r.nav_status
            hold_s = "available" if slug in slugs_with_holdings else r.holdings_status
            meta_s = "available" if r.scheme_name in meta_names else r.metadata_status
            if (nav_s, hold_s, meta_s) != (r.nav_status, r.holdings_status, r.metadata_status):
                r.nav_status = nav_s
                r.holdings_status = hold_s
                r.metadata_status = meta_s
                session.add(r)
                updated += 1
        session.commit()
    return updated


# Re-exports so callers can `from services.registry_service import load_holdings`
__all__ = [
    "add_funds",
    "backfill_missing",
    "list_tracked",
    "list_tracked_names",
    "load_holdings",
    "load_metadata",
    "load_registry",
    "reconcile_statuses",
    "remove_fund",
    "retry_unavailable",
    "save_to_registry",
]
