"""NAV data repository — load, save, fetch, and ensure NAV data.

Keyed on `scheme_code` internally; public functions take `scheme_names` and output `schemeName`
so name-joining callers (Portfolio, screener) don't change.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

import polars as pl
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import col, delete, func, select

from core.database import get_session
from core.frames import frame_from_rows
from core.models import AmfiScheme, MfNav
from core.timing import timeit
from data.exceptions import SourceThrottled
from data.fetchers.advisorkhoj import fetch_nav_from_advisorkhoj
from data.fetchers.mfapi import fetch_nav_from_mfapi, resolve_mfapi_code
from data.repositories.amfi import lookup_scheme_code_by_exact_name
from data.repositories.scheme_codes import resolve_codes, resolve_codes_with_synthetic

logger = logging.getLogger(__name__)


def _resolve_names(scheme_codes: list[int]) -> dict[int, str]:
    """Return scheme_code -> name via amfi_schemes."""
    if not scheme_codes:
        return {}
    with get_session() as session:
        rows = session.exec(
            select(AmfiScheme.scheme_code, AmfiScheme.scheme_name).where(col(AmfiScheme.scheme_code).in_(scheme_codes))
        ).all()
    return {r[0]: r[1] for r in rows}


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


def _upsert_nav_rows(session, df: pl.DataFrame, name_to_code: dict[str, int]) -> None:
    """Upsert (date, nav, schemeName[, scheme_code]) rows into mf_nav on the caller's session; a row's own code wins."""
    for row in df.iter_rows(named=True):
        code = row.get("scheme_code") or name_to_code[row["schemeName"]]
        stmt = (
            pg_insert(MfNav)
            .values(scheme_code=code, date=row["date"], nav=row["nav"])
            .on_conflict_do_update(
                index_elements=["scheme_code", "date"],
                set_={"nav": row["nav"]},
            )
        )
        session.exec(stmt)


def save_nav_df(df: pl.DataFrame) -> None:
    """Upsert NAV rows into the DB. Names with no AMFI match get a synthetic-negative code so the FK holds."""
    if df.height == 0:
        return
    name_to_code = resolve_codes_with_synthetic(_names_without_code(df))
    with get_session() as session:
        _upsert_nav_rows(session, df, name_to_code)
        session.commit()
    logger.debug("Saved %d NAV rows to database", df.height)


def _names_without_code(df: pl.DataFrame) -> list[str]:
    if "scheme_code" not in df.columns:
        return df["schemeName"].unique().to_list()
    return df.filter(pl.col("scheme_code").is_null())["schemeName"].unique().to_list()


def load_nav_by_codes(scheme_codes: list[int]) -> pl.DataFrame:
    """NAV as (date, nav, scheme_code) for exactly these codes."""
    rows = []
    if scheme_codes:
        with get_session() as session:
            rows = session.exec(
                select(MfNav.date, MfNav.nav, MfNav.scheme_code)
                .where(col(MfNav.scheme_code).in_(scheme_codes))
                .order_by(col(MfNav.date))
            ).all()
    return frame_from_rows(rows, {"date": pl.Date, "nav": pl.Float64, "scheme_code": pl.Int64})


def nav_record_stats(scheme_names: list[str] | None = None) -> dict[str, tuple[int, object]]:
    """{scheme_name: (record count, earliest date)} aggregated in SQL."""
    with get_session() as session:
        stmt = (
            select(AmfiScheme.scheme_name, func.count(col(MfNav.date)), func.min(col(MfNav.date)))
            .join(AmfiScheme, MfNav.scheme_code == AmfiScheme.scheme_code)
            .group_by(col(AmfiScheme.scheme_name))
        )
        if scheme_names:
            stmt = stmt.where(col(AmfiScheme.scheme_name).in_(scheme_names))
        rows = session.exec(stmt).all()
    return {name: (count, first) for name, count, first in rows}


def load_nav_df(scheme_names: list[str] | None = None) -> pl.DataFrame:
    """Load NAV as (date, nav, schemeName), JOINing mf_nav -> amfi_schemes for the name."""
    with get_session() as session:
        stmt = (
            select(MfNav.date, MfNav.nav, AmfiScheme.scheme_name)
            .join(AmfiScheme, MfNav.scheme_code == AmfiScheme.scheme_code)
            .order_by(col(MfNav.date))
        )
        if scheme_names:
            stmt = stmt.where(col(AmfiScheme.scheme_name).in_(scheme_names))
        rows = session.exec(stmt).all()

    return frame_from_rows(rows, {"date": pl.Date, "nav": pl.Float64, "schemeName": pl.Utf8})


def fetch_single_nav(scheme_name: str, scheme_code: int | None = None) -> pl.DataFrame:
    """Fetch NAV for one scheme from MFAPI, falling back to AdvisorKhoj; rows are tagged with the code used.

    `SourceThrottled` from MFAPI propagates. Pass `scheme_code` when known to avoid name-based sibling mix-ups.
    """
    known = scheme_code if scheme_code is not None else lookup_scheme_code_by_exact_name(scheme_name)
    tag: int | None = int(known) if known is not None else None
    if known is not None and int(known) <= 0:
        mfapi_code = None
    else:
        mfapi_code = str(known) if known is not None else resolve_mfapi_code(scheme_name)
    if mfapi_code:
        try:
            df = fetch_nav_from_mfapi(mfapi_code, scheme_name)
            return df.with_columns(pl.lit(tag, dtype=pl.Int64).alias("scheme_code"))
        except SourceThrottled:
            raise
        except Exception as e:
            logger.info("MFAPI failed for %s (code=%s): %s", scheme_name, mfapi_code, e)

    logger.debug("Falling back to AdvisorKhoj for NAV: %s", scheme_name)
    data = fetch_nav_from_advisorkhoj(scheme_name)
    df = nav_json_to_df(data["nav_data"], scheme_name)
    return df.with_columns(pl.lit(scheme_code, dtype=pl.Int64).alias("scheme_code"))


@timeit("nav.fetch_nav_parallel")
def fetch_nav_parallel(scheme_names: list[str], name_to_code: dict[str, int] | None = None) -> list[pl.DataFrame]:
    """Fetch NAV data for multiple schemes in parallel."""
    codes = name_to_code or resolve_codes(scheme_names)
    new_frames = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        future_to_scheme = {pool.submit(fetch_single_nav, scheme, codes.get(scheme)): scheme for scheme in scheme_names}
        for future in as_completed(future_to_scheme):
            scheme = future_to_scheme[future]
            try:
                df = future.result()
                new_frames.append(df)
            except Exception as e:
                logger.error("Failed to fetch NAV for %s: %s", scheme, e)
    return new_frames


def _recompute_metrics_for(scheme_names: list[str]) -> None:
    """Best-effort metrics recompute; failures are logged, never break the NAV save."""
    if not scheme_names:
        return
    try:
        from services.mf_metrics import recompute_metrics  # noqa: PLC0415

        recompute_metrics(scheme_names)
    except Exception:
        logger.exception("Post-NAV-save metrics recompute failed for %d scheme(s)", len(scheme_names))


@timeit("nav.ensure_nav_data")
def ensure_nav_data(scheme_names: list[str]) -> pl.DataFrame:
    """DB-first: load existing NAV, fetch only missing schemes, recompute metrics, return all."""
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
    """Re-fetch and replace NAV; only schemes that fetch successfully are replaced (delete + insert in one
    transaction)."""
    new_frames = fetch_nav_parallel(scheme_names)
    fetched_schemes = [name for df in new_frames if df.height for name in df["schemeName"].unique().to_list()]
    if not fetched_schemes:
        return load_nav_df(scheme_names)

    name_to_code = resolve_codes_with_synthetic(fetched_schemes)
    codes = [name_to_code[n] for n in fetched_schemes if n in name_to_code]
    with get_session() as session:
        if codes:
            session.exec(delete(MfNav).where(col(MfNav.scheme_code).in_(codes)))
        for df in new_frames:
            if df.height:
                _upsert_nav_rows(session, df, name_to_code)
        session.commit()

    _recompute_metrics_for(fetched_schemes)
    return load_nav_df(scheme_names)


def count_distinct_nav_schemes() -> int:
    """Number of distinct schemes that have any NAV history."""
    with get_session() as session:
        return int(session.exec(select(func.count(func.distinct(MfNav.scheme_code)))).one() or 0)


def last_nav_date_by_code(scheme_codes: list[int]) -> dict[int, object]:
    """{scheme_code: latest NAV date}."""
    if not scheme_codes:
        return {}
    with get_session() as session:
        rows = session.exec(
            select(MfNav.scheme_code, func.max(MfNav.date))
            .where(col(MfNav.scheme_code).in_(scheme_codes))
            .group_by(MfNav.scheme_code)
        ).all()
    return {int(r[0]): r[1] for r in rows if r[1] is not None}


def last_nav_date_by_name(scheme_names: list[str]) -> dict:
    """{scheme_name: latest NAV date}."""
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
