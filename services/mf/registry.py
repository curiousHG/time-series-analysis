"""Registry — single source of truth for tracked funds.

`mf_registry` is keyed on `scheme_code`; public APIs take/return scheme_name and resolve
through `amfi_schemes`. Funds without an AMFI code get synthetic negative codes so the FK holds.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

import polars as pl

from core.enums import SourceStatus
from data.repositories.amfi import get_scheme_code
from data.repositories.registry import (
    delete_fund,
    load_tracked_rows,
    reconcile_registry_statuses,
    save_registry_row,
    set_registry_statuses,
)
from data.repositories.scheme_codes import resolve_or_mint_code
from mutual_funds.display import make_slug, unique_short_names
from services.constants import DORMANT_AFTER_DAYS

logger = logging.getLogger(__name__)


def list_tracked() -> pl.DataFrame:
    """All rows from mf_registry as a polars DataFrame, with scheme_name JOINed in."""
    rows = load_tracked_rows()
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
    return get_scheme_code(scheme_name)


def set_status(scheme_name: str, **statuses: SourceStatus) -> None:
    if not statuses:
        return
    code = resolve_scheme_code(scheme_name)
    if code is None:
        return
    set_registry_statuses(code, **statuses)


def upsert_registry(scheme_name: str, scheme_code: int | None = None) -> int:
    """Insert (or update) an mf_registry row keyed on scheme_code. Returns the resolved code."""
    code = scheme_code if scheme_code is not None else resolve_or_mint_code(scheme_name)
    save_registry_row(code)
    return code


def mark_nav_unavailable(scheme_name: str) -> None:
    """Record that no source knows this fund's NAV. The routine refresh leaves 'unavailable'
    funds alone; the Settings retry picker is the way back."""
    set_status(scheme_name, nav_status=SourceStatus.UNAVAILABLE)


def remove_fund(scheme_name: str) -> None:
    """Drop a fund from the registry and cascade-delete its NAV/holdings/metadata rows."""
    code = resolve_scheme_code(scheme_name)
    if code is None:
        logger.warning("remove_fund: no scheme_code for %s — nothing to delete", scheme_name)
        return
    delete_fund(code)
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
        (pl.col("navStatus") == SourceStatus.UNAVAILABLE)
        | (pl.col("holdingsStatus") == SourceStatus.UNAVAILABLE)
        | (pl.col("metadataStatus") == SourceStatus.UNAVAILABLE)
    )


def reconcile_statuses() -> int:
    """Set nav/holdings/metadata statuses based on actual data presence."""
    return reconcile_registry_statuses()
