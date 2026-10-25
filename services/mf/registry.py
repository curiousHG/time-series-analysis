"""Registry — single source of truth for tracked funds.

`mf_registry` is keyed on `scheme_code`; public APIs take/return scheme_name and resolve
through `amfi_schemes`. Funds without an AMFI code get synthetic negative codes so the FK holds.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

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
from data.repositories.scheme_codes import resolve_or_mint_code
from mutual_funds.display import make_slug, unique_short_names
from services.constants import DORMANT_AFTER_DAYS

logger = logging.getLogger(__name__)


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
                AmfiScheme.nav_date,
                MfRegistry.advisorkhoj_slug,
                AmfiScheme.nav,
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
                "dormant": pl.Boolean,
                "advisorkhojSlug": pl.Utf8,
            }
        )
    cutoff = date.today() - timedelta(days=DORMANT_AFTER_DAYS)
    return pl.DataFrame(
        {
            "schemeName": [r[0] for r in rows],
            "schemeCode": [r[1] for r in rows],
            "navStatus": [r[2] for r in rows],
            "holdingsStatus": [r[3] for r in rows],
            "metadataStatus": [r[4] for r in rows],
            "addedAt": [r[5] for r in rows],
            "lastAttemptedAt": [r[6] for r in rows],
            "dormant": [(r[7] is not None and r[7] < cutoff) or (r[9] is not None and r[9] <= 0) for r in rows],
            "advisorkhojSlug": [r[8] for r in rows],
        }
    )


def list_active() -> pl.DataFrame:
    """`list_tracked` minus dormant schemes — the set every fetch and freshness path works on."""
    df = list_tracked()
    return df if df.is_empty() else df.filter(~pl.col("dormant"))


def resolve_scheme_code(scheme_name: str) -> int | None:
    """Look up scheme_code in amfi_schemes by exact name match."""
    with get_session() as session:
        row = session.exec(select(AmfiScheme.scheme_code).where(AmfiScheme.scheme_name == scheme_name)).first()
    return int(row) if row is not None else None


def set_status(scheme_name: str, **statuses: str) -> None:
    if not statuses:
        return
    code = resolve_scheme_code(scheme_name)
    if code is None:
        return
    with get_session() as session:
        row = session.get(MfRegistry, code)
        if row is None:
            return
        for k, v in statuses.items():
            setattr(row, k, v)
        row.last_attempted_at = datetime.now(UTC).replace(tzinfo=None)
        session.add(row)
        session.commit()


def upsert_registry(scheme_name: str, scheme_code: int | None = None) -> int:
    """Insert (or update) an mf_registry row keyed on scheme_code. Returns the resolved code."""
    code = scheme_code if scheme_code is not None else resolve_or_mint_code(scheme_name)
    with get_session() as session:
        stmt = (
            pg_insert(MfRegistry)
            .values(
                scheme_code=code,
                nav_status="pending",
                holdings_status="pending",
                metadata_status="pending",
                added_at=datetime.now(UTC).replace(tzinfo=None),
            )
            .on_conflict_do_nothing(index_elements=["scheme_code"])
        )
        session.exec(stmt)
        session.commit()
    return code


def mark_nav_unavailable(scheme_name: str) -> None:
    """Record that no source knows this fund's NAV. The routine refresh leaves 'unavailable'
    funds alone; the Settings retry picker is the way back."""
    set_status(scheme_name, nav_status="unavailable")


def remove_fund(scheme_name: str) -> None:
    """Drop a fund from the registry and cascade-delete its NAV/holdings/metadata rows."""
    code = resolve_scheme_code(scheme_name)
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


def load_registry() -> pl.DataFrame:
    """Old-shape (schemeName, schemeSlug, shortName) view — retained until UI migrates."""
    df = list_tracked()
    if df.height == 0:
        return pl.DataFrame(schema={"schemeName": pl.Utf8, "schemeSlug": pl.Utf8, "shortName": pl.Utf8})
    short = unique_short_names(df["schemeName"].to_list())
    return df.select(
        pl.col("schemeName"),
        pl.col("schemeName").map_elements(make_slug, return_dtype=pl.Utf8).alias("schemeSlug"),
        pl.col("schemeName").replace_strict(short, return_dtype=pl.Utf8).alias("shortName"),
    )


def save_to_registry(scheme_names: list[str]) -> None:
    """Register funds with `pending` status. Data is pulled later by `backfill_missing`
    (Screener page) or the Settings refresh actions."""
    if not scheme_names:
        return
    for name in scheme_names:
        upsert_registry(name)


def list_unavailable_funds() -> pl.DataFrame:
    """Tracked funds with at least one source still marked 'unavailable'.
    Drives the Settings → "Retry unavailable sources" picker."""
    df = list_active()
    if df.is_empty():
        return df
    return df.filter(
        (pl.col("navStatus") == "unavailable")
        | (pl.col("holdingsStatus") == "unavailable")
        | (pl.col("metadataStatus") == "unavailable")
    )


def reconcile_statuses() -> int:
    """Set nav/holdings/metadata statuses based on actual data presence."""
    with get_session() as session:
        regs = session.exec(select(MfRegistry)).all()
        nav_codes = set(session.exec(select(MfNav.scheme_code).distinct()).all())
        meta_codes = set(session.exec(select(MfMetadata.scheme_code)).all())
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
