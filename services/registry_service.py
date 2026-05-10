"""Registry service — single source of truth for tracked funds.

Phase 2: `mf_registry` is keyed on `scheme_code`. Public APIs still take/return scheme_name
for caller convenience — every CRUD path resolves through `amfi_schemes`. Tracked funds
without an AMFI code are auto-assigned synthetic negatives so the FK never violates.
"""

import contextlib
import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import polars as pl
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import col, delete, func, select

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
from data.repositories.nav import _fetch_single_nav, save_nav_df
from mutual_funds.display import make_slug
from mutual_funds.holdings import (
    normalize_asset_allocation,
    normalize_holdings,
    normalize_sector_allocation,
)

logger = logging.getLogger("services.registry_service")


# ---- Read ----


def list_tracked() -> pl.DataFrame:
    """All rows from mf_registry as a polars DataFrame, with scheme_name JOINed in."""
    with get_session() as session:
        rows = session.exec(
            select(
                AmfiScheme.scheme_name,
                MfRegistry.scheme_code,
                MfRegistry.nav_status,
                MfRegistry.holdings_status,
                MfRegistry.metadata_status,
                MfRegistry.added_at,
                MfRegistry.last_attempted_at,
            )
            .join(AmfiScheme, MfRegistry.scheme_code == AmfiScheme.scheme_code)
            .order_by(AmfiScheme.scheme_name)
        ).all()
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
            "schemeName": [r[0] for r in rows],
            "schemeCode": [r[1] for r in rows],
            "navStatus": [r[2] for r in rows],
            "holdingsStatus": [r[3] for r in rows],
            "metadataStatus": [r[4] for r in rows],
            "addedAt": [r[5] for r in rows],
            "lastAttemptedAt": [r[6] for r in rows],
        }
    )


# ---- Status helpers ----


def _resolve_scheme_code(scheme_name: str) -> int | None:
    """Look up scheme_code in amfi_schemes by exact name match."""
    with get_session() as session:
        row = session.exec(
            select(AmfiScheme.scheme_code).where(AmfiScheme.scheme_name == scheme_name)
        ).first()
    return int(row) if row is not None else None


def _resolve_or_mint_code(scheme_name: str) -> int:
    """Resolve scheme_name -> scheme_code; mint a synthetic negative if not found."""
    code = _resolve_scheme_code(scheme_name)
    if code is not None:
        return code
    with get_session() as session:
        min_code = session.exec(select(func.min(AmfiScheme.scheme_code))).one() or 0
        next_neg = min(min_code, 0) - 1
        session.exec(
            pg_insert(AmfiScheme)
            .values(scheme_code=next_neg, scheme_name=scheme_name)
            .on_conflict_do_nothing(index_elements=["scheme_code"])
        )
        session.commit()
    # New scheme row invalidates the slug → scheme_code map.
    from data.repositories.holdings import clear_slug_cache

    clear_slug_cache()
    logger.warning("Minted synthetic code %d for previously-unknown scheme %s", next_neg, scheme_name)
    return next_neg


def _set_status(scheme_name: str, **statuses: str) -> None:
    if not statuses:
        return
    code = _resolve_scheme_code(scheme_name)
    if code is None:
        return
    with get_session() as session:
        row = session.get(MfRegistry, code)
        if row is None:
            return
        for k, v in statuses.items():
            setattr(row, k, v)
        row.last_attempted_at = datetime.utcnow()
        session.add(row)
        session.commit()


def _upsert_registry(scheme_name: str, scheme_code: int | None = None) -> int:
    """Insert (or update) an mf_registry row keyed on scheme_code. Returns the resolved code."""
    code = scheme_code if scheme_code is not None else _resolve_or_mint_code(scheme_name)
    with get_session() as session:
        stmt = (
            pg_insert(MfRegistry)
            .values(
                scheme_code=code,
                nav_status="pending",
                holdings_status="pending",
                metadata_status="pending",
                added_at=datetime.utcnow(),
            )
            .on_conflict_do_nothing(index_elements=["scheme_code"])
        )
        session.exec(stmt)
        session.commit()
    return code


# ---- Fetchers wired to status updates ----


def _fetch_nav(scheme_name: str) -> str:
    try:
        df = _fetch_single_nav(scheme_name)
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


def retry_unavailable(scheme_name: str) -> dict[str, str]:
    """Retry only the sources currently marked 'unavailable' for a fund."""
    code = _resolve_scheme_code(scheme_name)
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

    results: dict[str, str] = {}
    if targets["nav_status"] == "unavailable":
        results["nav_status"] = _fetch_nav(scheme_name)
    if targets["holdings_status"] == "unavailable":
        results["holdings_status"] = _fetch_holdings(scheme_name)
    if targets["metadata_status"] == "unavailable":
        results["metadata_status"] = _fetch_metadata(scheme_name)

    if results:
        _set_status(scheme_name, **results)
    return results


def backfill_missing(
    *,
    scheme_names: list[str] | None = None,
    sources: tuple[str, ...] = ("nav", "metadata"),
    max_per_run: int = 50,
    submit_delay: float = 0.05,
    max_workers: int = 8,
    progress_cb: Callable[[int, int, str, str], None] | None = None,
) -> dict[str, list[str]]:
    """Fetch missing data for tracked funds with rate limiting.

    Concurrency tuned from empirical probes:
      • MFAPI (NAV)         — sweet spot at 16 workers; we cap at 8 since work mixes with metadata.
      • AdvisorKhoj overview — server starts queueing at 8+ workers (p50 latency doubles).
      • AdvisorKhoj holdings — handles 16, but holdings is excluded from the default sources.
    8 workers + a 50ms submit delay is the safe blend for the screener "Fetch top N" button.
    """
    if scheme_names is not None:
        for name in scheme_names:
            _upsert_registry(name)

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


def remove_fund(scheme_name: str) -> None:
    """Drop a fund from the registry and cascade-delete its NAV/holdings/metadata rows."""
    code = _resolve_scheme_code(scheme_name)
    if code is None:
        logger.warning("remove_fund: no scheme_code for %s — nothing to delete", scheme_name)
        return
    with get_session() as session:
        session.exec(delete(MfNav).where(col(MfNav.scheme_code) == code))
        session.exec(delete(MfMetadata).where(col(MfMetadata.scheme_code) == code))
        session.exec(delete(MfHolding).where(col(MfHolding.scheme_code) == code))
        session.exec(delete(MfSectorAllocation).where(col(MfSectorAllocation.scheme_code) == code))
        session.exec(delete(MfAssetAllocation).where(col(MfAssetAllocation.scheme_code) == code))
        session.exec(delete(MfRegistry).where(col(MfRegistry.scheme_code) == code))
        session.commit()
    logger.info("Removed fund: %s (code %d)", scheme_name, code)


# ---- Compatibility shim while UI is being migrated ----


def load_registry() -> pl.DataFrame:
    """Old-shape (schemeName, schemeSlug, shortName) view — retained until UI migrates."""
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
    """Register funds with `pending` status. Data is pulled later by `backfill_missing`
    (Screener page) or the Settings refresh actions."""
    if not scheme_names:
        return
    for name in scheme_names:
        _upsert_registry(name)


def list_unavailable_funds() -> pl.DataFrame:
    """Tracked funds with at least one source still marked 'unavailable'.
    Drives the Settings → "Retry unavailable sources" picker."""
    df = list_tracked()
    if df.is_empty():
        return df
    return df.filter(
        (pl.col("navStatus") == "unavailable")
        | (pl.col("holdingsStatus") == "unavailable")
        | (pl.col("metadataStatus") == "unavailable")
    )


# ---- Status backfill from data presence ----


def reconcile_statuses() -> int:
    """Set nav/holdings/metadata statuses based on actual data presence."""
    with get_session() as session:
        regs = session.exec(select(MfRegistry)).all()
        nav_codes = set(session.exec(select(MfNav.scheme_code).distinct()).all())
        meta_codes = set(session.exec(select(MfMetadata.scheme_code)).all())
        # Phase 3: holdings are keyed on scheme_code now (no slug column).
        codes_with_holdings = set(session.exec(select(MfHolding.scheme_code).distinct()).all())

        updated = 0
        for r in regs:
            nav_s = "available" if r.scheme_code in nav_codes else r.nav_status
            hold_s = "available" if r.scheme_code in codes_with_holdings else r.holdings_status
            meta_s = "available" if r.scheme_code in meta_codes else r.metadata_status
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
    "backfill_missing",
    "list_tracked",
    "load_holdings",
    "load_metadata",
    "load_registry",
    "reconcile_statuses",
    "remove_fund",
    "retry_unavailable",
    "save_to_registry",
]
