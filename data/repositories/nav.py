"""NAV data repository — load, save, fetch, and ensure NAV data."""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

import polars as pl
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import col, delete, select

from core.database import get_session
from core.models import MfNav, SchemeCodeMap
from data.fetchers.mutual_fund import (
    fetch_nav_from_advisorkhoj,
    fetch_nav_from_mfapi,
    resolve_mfapi_code,
)

logger = logging.getLogger("data.repositories.nav")


# ---- Scheme code map ----


def _load_scheme_code_map() -> dict[str, str]:
    with get_session() as session:
        rows = session.exec(select(SchemeCodeMap)).all()
        return {r.scheme_name: r.scheme_code for r in rows}


def _save_scheme_code_map(code_map: dict[str, str]):
    with get_session() as session:
        for name, code in code_map.items():
            stmt = (
                pg_insert(SchemeCodeMap)
                .values(scheme_name=name, scheme_code=code)
                .on_conflict_do_update(
                    index_elements=["scheme_name"],
                    set_={"scheme_code": code},
                )
            )
            session.exec(stmt)
        session.commit()


def _get_or_resolve_scheme_code(scheme_name: str, code_map: dict[str, str]) -> str | None:
    if scheme_name in code_map:
        return code_map[scheme_name]
    code = resolve_mfapi_code(scheme_name)
    if code:
        code_map[scheme_name] = code
    return code


# ---- NAV data ----


def nav_json_to_df(nav_json: list[list], scheme_name: str) -> pl.DataFrame:
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


def save_nav_df(df: pl.DataFrame):
    """Upsert NAV rows into the database."""
    if df.height == 0:
        return
    with get_session() as session:
        for row in df.iter_rows(named=True):
            stmt = (
                pg_insert(MfNav)
                .values(date=row["date"], nav=row["nav"], scheme_name=row["schemeName"])
                .on_conflict_do_update(
                    index_elements=["date", "scheme_name"],
                    set_={"nav": row["nav"]},
                )
            )
            session.exec(stmt)
        session.commit()
    logger.info("Saved %d NAV rows to database", df.height)


def load_nav_df(scheme_names: list[str] | None = None) -> pl.DataFrame:
    """Load NAV data from database, optionally filtered by scheme names."""
    with get_session() as session:
        stmt = select(MfNav).order_by(col(MfNav.date))
        if scheme_names:
            stmt = stmt.where(col(MfNav.scheme_name).in_(scheme_names))
        rows = session.exec(stmt).all()

    if not rows:
        return pl.DataFrame(schema={"date": pl.Date, "nav": pl.Float64, "schemeName": pl.Utf8})
    return pl.DataFrame(
        {
            "date": [r.date for r in rows],
            "nav": [r.nav for r in rows],
            "schemeName": [r.scheme_name for r in rows],
        }
    )


def _fetch_single_nav(scheme_name: str, code_map: dict[str, str]) -> pl.DataFrame:
    """Fetch NAV for a single scheme. Tries MFAPI first, falls back to AdvisorKhoj."""
    scheme_code = _get_or_resolve_scheme_code(scheme_name, code_map)
    if scheme_code:
        try:
            return fetch_nav_from_mfapi(scheme_code, scheme_name)
        except Exception as e:
            logger.warning("MFAPI failed for %s (code=%s): %s", scheme_name, scheme_code, e)

    logger.info("Falling back to AdvisorKhoj for NAV: %s", scheme_name)
    data = fetch_nav_from_advisorkhoj(scheme_name)
    return nav_json_to_df(data["nav_data"], scheme_name)


def fetch_nav_parallel(scheme_names: list[str]) -> list[pl.DataFrame]:
    """Fetch NAV data for multiple schemes in parallel."""
    code_map = _load_scheme_code_map()
    new_frames = []

    with ThreadPoolExecutor(max_workers=4) as pool:
        future_to_scheme = {pool.submit(_fetch_single_nav, scheme, code_map): scheme for scheme in scheme_names}
        for future in as_completed(future_to_scheme):
            scheme = future_to_scheme[future]
            try:
                df = future.result()
                new_frames.append(df)
            except Exception as e:
                logger.error("Failed to fetch NAV for %s: %s", scheme, e)

    _save_scheme_code_map(code_map)
    return new_frames


def ensure_nav_data(scheme_names: list[str]) -> pl.DataFrame:
    """Ensures NAV data exists in DB for given scheme names."""
    nav_df = load_nav_df(scheme_names)
    existing = nav_df.select("schemeName").unique().to_series().to_list() if nav_df.height else []

    missing = list(set(scheme_names) - set(existing))
    if not missing:
        return nav_df

    new_frames = fetch_nav_parallel(missing)
    for df in new_frames:
        save_nav_df(df)

    return load_nav_df(scheme_names)


def refresh_nav_data(scheme_names: list[str]) -> pl.DataFrame:
    """Re-fetch NAV data for given schemes, replacing existing entries."""
    with get_session() as session:
        session.exec(delete(MfNav).where(col(MfNav.scheme_name).in_(scheme_names)))
        session.commit()

    new_frames = fetch_nav_parallel(scheme_names)
    for df in new_frames:
        save_nav_df(df)

    return load_nav_df(scheme_names)
