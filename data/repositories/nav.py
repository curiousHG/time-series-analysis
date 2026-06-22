"""NAV data repository — load, save, fetch, and ensure NAV data.

Phase 2 normalisation: NAV is keyed on `scheme_code` (int). Public functions still take
`scheme_names: list[str]` for caller convenience — we resolve to codes internally via
`amfi_schemes`. The output DataFrame keeps `schemeName` so downstream code that joins on
the scheme name (Portfolio analytics, screener) doesn't have to change.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import polars as pl
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import col, delete, func, select

from core.database import get_session
from core.models import AmfiScheme, MfNav
from core.timing import timeit
from data.fetchers.mutual_fund import (
    fetch_nav_from_advisorkhoj,
    fetch_nav_from_mfapi,
    resolve_mfapi_code,
)
from data.repositories.amfi import lookup_scheme_code_by_exact_name

logger = logging.getLogger("data.repositories.nav")


# ---- name <-> code resolution helpers (replaces the dropped scheme_code_map cache) ----


def _resolve_codes(scheme_names: list[str]) -> dict[str, int]:
    """Return name -> scheme_code via amfi_schemes for the given names."""
    if not scheme_names:
        return {}
    with get_session() as session:
        rows = session.exec(
            select(AmfiScheme.scheme_name, AmfiScheme.scheme_code).where(
                col(AmfiScheme.scheme_name).in_(scheme_names)
            )
        ).all()
    return {r[0]: r[1] for r in rows}


def _resolve_names(scheme_codes: list[int]) -> dict[int, str]:
    """Return scheme_code -> name via amfi_schemes."""
    if not scheme_codes:
        return {}
    with get_session() as session:
        rows = session.exec(
            select(AmfiScheme.scheme_code, AmfiScheme.scheme_name).where(
                col(AmfiScheme.scheme_code).in_(scheme_codes)
            )
        ).all()
    return {r[0]: r[1] for r in rows}


def _get_or_resolve_scheme_code(scheme_name: str) -> str | None:
    """Resolve a scheme_name to its MFAPI/AMFI code (str). Direct AMFI lookup, then fuzzy."""
    code = lookup_scheme_code_by_exact_name(scheme_name)
    if code:
        return code
    return resolve_mfapi_code(scheme_name)


# ---- NAV data ----


def nav_json_to_df(nav_json: list[list], scheme_name: str) -> pl.DataFrame:
    """Convert MFAPI raw nav payload to a (date, nav, schemeName) DataFrame."""
    cleaned = [{"ts_ms": int(row[0]), "nav": float(row[1])} for row in nav_json if row and row[1] is not None]
    return (
        pl.DataFrame(cleaned)
        .with_columns(
            pl.from_epoch(pl.col("ts_ms"), time_unit="ms").dt.date().alias("date"),
            pl.col("nav").alias("nav"),
            pl.lit(scheme_name).alias("schemeName"),
        )
        .select("date", "nav", "schemeName")
        .sort("date")
        .unique(subset=["date", "schemeName"], keep="last")
    )


def save_nav_df(df: pl.DataFrame) -> None:
    """Upsert NAV rows into the database. df has columns: date, nav, schemeName.

    Resolves schemeName -> scheme_code via amfi_schemes. Schemes with no AMFI match are
    inserted as synthetic-negative rows so the FK never violates.
    """
    if df.height == 0:
        return

    distinct_names = df["schemeName"].unique().to_list()
    name_to_code = _resolve_codes(distinct_names)

    # Auto-assign synthetic negative codes for any name not in amfi_schemes (e.g. brand-new
    # tracked fund whose AMFI master row hasn't synced yet).
    missing_names = [n for n in distinct_names if n not in name_to_code]
    if missing_names:
        with get_session() as session:
            min_code = (
                session.exec(select(func.min(AmfiScheme.scheme_code))).one() or 0
            )
            next_neg = min(min_code, 0) - 1
            for name in missing_names:
                session.exec(
                    pg_insert(AmfiScheme)
                    .values(scheme_code=next_neg, scheme_name=name, db_added_at=datetime.utcnow())
                    .on_conflict_do_nothing(index_elements=["scheme_code"])
                )
                name_to_code[name] = next_neg
                next_neg -= 1
            session.commit()
        # New scheme rows invalidate the slug → scheme_code map.
        from data.repositories.holdings import clear_slug_cache

        clear_slug_cache()
        logger.warning(
            "Assigned synthetic negative codes to %d new scheme(s) on NAV save: %s",
            len(missing_names),
            missing_names[:3],
        )

    with get_session() as session:
        for row in df.iter_rows(named=True):
            code = name_to_code[row["schemeName"]]
            stmt = (
                pg_insert(MfNav)
                .values(scheme_code=code, date=row["date"], nav=row["nav"])
                .on_conflict_do_update(
                    index_elements=["scheme_code", "date"],
                    set_={"nav": row["nav"]},
                )
            )
            session.exec(stmt)
        session.commit()
    logger.info("Saved %d NAV rows to database", df.height)


@timeit("nav.load_nav_df")
def load_nav_df(scheme_names: list[str] | None = None) -> pl.DataFrame:
    """Load NAV data; output schema unchanged (date, nav, schemeName).

    JOINs `mf_nav -> amfi_schemes` to project scheme_name from the dim. Filtering by
    scheme_names hits amfi_schemes via the JOIN; cheap because amfi_schemes is small
    and indexed on scheme_name.
    """
    with get_session() as session:
        stmt = (
            select(MfNav.date, MfNav.nav, AmfiScheme.scheme_name)
            .join(AmfiScheme, MfNav.scheme_code == AmfiScheme.scheme_code)
            .order_by(col(MfNav.date))
        )
        if scheme_names:
            stmt = stmt.where(col(AmfiScheme.scheme_name).in_(scheme_names))
        rows = session.exec(stmt).all()

    if not rows:
        return pl.DataFrame(schema={"date": pl.Date, "nav": pl.Float64, "schemeName": pl.Utf8})
    return pl.DataFrame(
        {
            "date": [r[0] for r in rows],
            "nav": [r[1] for r in rows],
            "schemeName": [r[2] for r in rows],
        }
    )


def fetch_single_nav(scheme_name: str) -> pl.DataFrame:
    """Fetch NAV for a single scheme. Tries MFAPI first, falls back to AdvisorKhoj."""
    scheme_code = _get_or_resolve_scheme_code(scheme_name)
    if scheme_code:
        try:
            return fetch_nav_from_mfapi(scheme_code, scheme_name)
        except Exception as e:
            logger.warning("MFAPI failed for %s (code=%s): %s", scheme_name, scheme_code, e)

    logger.info("Falling back to AdvisorKhoj for NAV: %s", scheme_name)
    data = fetch_nav_from_advisorkhoj(scheme_name)
    return nav_json_to_df(data["nav_data"], scheme_name)


@timeit("nav.fetch_nav_parallel")
def fetch_nav_parallel(scheme_names: list[str]) -> list[pl.DataFrame]:
    """Fetch NAV data for multiple schemes in parallel."""
    new_frames = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        future_to_scheme = {pool.submit(fetch_single_nav, scheme): scheme for scheme in scheme_names}
        for future in as_completed(future_to_scheme):
            scheme = future_to_scheme[future]
            try:
                df = future.result()
                new_frames.append(df)
            except Exception as e:
                logger.error("Failed to fetch NAV for %s: %s", scheme, e)
    return new_frames


def _recompute_metrics_for(scheme_names: list[str]) -> None:
    """Best-effort metrics recompute. Logged-only on failure — never break the NAV save."""
    if not scheme_names:
        return
    try:
        from services.mf_metrics import recompute_metrics

        recompute_metrics(scheme_names)
    except Exception:
        logger.exception("Post-NAV-save metrics recompute failed for %d scheme(s)", len(scheme_names))


@timeit("nav.ensure_nav_data")
def ensure_nav_data(scheme_names: list[str]) -> pl.DataFrame:
    """Ensures NAV data exists in DB for given scheme names."""
    nav_df = load_nav_df(scheme_names)
    existing = nav_df.select("schemeName").unique().to_series().to_list() if nav_df.height else []
    missing = list(set(scheme_names) - set(existing))
    if not missing:
        return nav_df

    new_frames = fetch_nav_parallel(missing)
    saved_schemes: list[str] = []
    for df in new_frames:
        save_nav_df(df)
        if df.height:
            saved_schemes.extend(df["schemeName"].unique().to_list())

    _recompute_metrics_for(saved_schemes)
    return load_nav_df(scheme_names)


def refresh_nav_data(scheme_names: list[str]) -> pl.DataFrame:
    """Re-fetch NAV data for given schemes, replacing existing entries.

    Only schemes that fetch successfully are deleted/replaced. A transient upstream
    failure should not erase the last good local NAV history.
    """
    new_frames = fetch_nav_parallel(scheme_names)
    fetched_schemes = [name for df in new_frames if df.height for name in df["schemeName"].unique().to_list()]

    name_to_code = _resolve_codes(fetched_schemes)
    codes = list(name_to_code.values())
    if codes:
        with get_session() as session:
            session.exec(delete(MfNav).where(col(MfNav.scheme_code).in_(codes)))
            session.commit()

    saved_schemes: list[str] = []
    for df in new_frames:
        save_nav_df(df)
        if df.height:
            saved_schemes.extend(df["schemeName"].unique().to_list())

    _recompute_metrics_for(saved_schemes)
    return load_nav_df(scheme_names)


def count_distinct_nav_schemes() -> int:
    """Number of distinct schemes that have any NAV history."""
    with get_session() as session:
        return int(session.exec(select(func.count(func.distinct(MfNav.scheme_code)))).one() or 0)


def last_nav_date_by_name(scheme_names: list[str]) -> dict:
    """Map name → most-recent NAV date already in mf_nav (joined via amfi_schemes).
    Used by the sync service to do incremental NAV updates."""
    if not scheme_names:
        return {}
    with get_session() as session:
        rows = session.exec(
            select(AmfiScheme.scheme_name, func.max(MfNav.date))
            .join(AmfiScheme, MfNav.scheme_code == AmfiScheme.scheme_code)
            .where(col(AmfiScheme.scheme_name).in_(scheme_names))
            .group_by(AmfiScheme.scheme_name)
        ).all()
    return {r[0]: r[1] for r in rows if r[1] is not None}
