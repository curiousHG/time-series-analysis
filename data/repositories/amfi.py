"""AMFI master data store — sync, ISIN lookup, name search."""

import logging

import polars as pl
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import col, func, select

from core.database import get_session
from core.models import AmfiScheme
from data.fetchers.mutual_fund import fetch_amfi_master

logger = logging.getLogger("data.store.amfi")


def sync_amfi_master() -> int:
    """Fetch AMFI NAVAll.txt and upsert all schemes into DB. Returns count."""
    schemes = fetch_amfi_master()

    with get_session() as session:
        for s in schemes:
            stmt = (
                pg_insert(AmfiScheme)
                .values(**s)
                .on_conflict_do_update(
                    index_elements=["scheme_code"],
                    set_={
                        "isin_growth": s["isin_growth"],
                        "isin_reinvestment": s["isin_reinvestment"],
                        "scheme_name": s["scheme_name"],
                        "nav": s["nav"],
                        "nav_date": s["nav_date"],
                        "fund_house": s["fund_house"],
                        "category": s["category"],
                    },
                )
            )
            session.exec(stmt)
        session.commit()

    logger.info("Synced %d AMFI schemes to database", len(schemes))
    return len(schemes)


def lookup_by_isin(isin: str) -> AmfiScheme | None:
    """Find a scheme by ISIN (growth or reinvestment)."""
    with get_session() as session:
        result = session.exec(select(AmfiScheme).where(col(AmfiScheme.isin_growth) == isin)).first()
        if result:
            return result

        result = session.exec(select(AmfiScheme).where(col(AmfiScheme.isin_reinvestment) == isin)).first()
        return result


def lookup_by_name(query: str) -> list[AmfiScheme]:
    """Search schemes by name (case-insensitive LIKE)."""
    with get_session() as session:
        return list(session.exec(select(AmfiScheme).where(col(AmfiScheme.scheme_name).ilike(f"%{query}%"))).all())


def search_amfi(query: str, limit: int = 50) -> pl.DataFrame:
    """Fuzzy-search AMFI schemes by name.

    Uses pg_trgm similarity ranking (typo-tolerant). Falls back to ILIKE if pg_trgm
    isn't available. Returns columns: schemeName, schemeCode, fundHouse, category,
    isinGrowth, score (similarity 0-1).
    """
    from sqlalchemy import text as sql_text

    if not query or len(query.strip()) < 2:
        return pl.DataFrame(
            schema={
                "schemeName": pl.Utf8,
                "schemeCode": pl.Int64,
                "fundHouse": pl.Utf8,
                "category": pl.Utf8,
                "isinGrowth": pl.Utf8,
                "score": pl.Float64,
            }
        )

    q = query.strip()
    with get_session() as session:
        try:
            rows = session.exec(
                sql_text(
                    """
                    SELECT scheme_name, scheme_code, fund_house, category, isin_growth,
                           similarity(scheme_name, :q) AS score
                    FROM amfi_schemes
                    WHERE scheme_name % :q OR scheme_name ILIKE :pattern
                    ORDER BY score DESC, length(scheme_name) ASC
                    LIMIT :limit
                    """
                ).bindparams(q=q, pattern=f"%{q}%", limit=limit)
            ).all()
        except Exception:
            # pg_trgm unavailable — plain ILIKE fallback
            rows = session.exec(
                sql_text(
                    """
                    SELECT scheme_name, scheme_code, fund_house, category, isin_growth,
                           0.0 AS score
                    FROM amfi_schemes
                    WHERE scheme_name ILIKE :pattern
                    ORDER BY length(scheme_name) ASC
                    LIMIT :limit
                    """
                ).bindparams(pattern=f"%{q}%", limit=limit)
            ).all()

    if not rows:
        return pl.DataFrame(
            schema={
                "schemeName": pl.Utf8,
                "schemeCode": pl.Int64,
                "fundHouse": pl.Utf8,
                "category": pl.Utf8,
                "isinGrowth": pl.Utf8,
                "score": pl.Float64,
            }
        )

    return pl.DataFrame(
        {
            "schemeName": [r[0] for r in rows],
            "schemeCode": [r[1] for r in rows],
            "fundHouse": [r[2] for r in rows],
            "category": [r[3] for r in rows],
            "isinGrowth": [r[4] for r in rows],
            "score": [float(r[5]) if r[5] is not None else 0.0 for r in rows],
        }
    )


def lookup_scheme_code_by_exact_name(name: str) -> str | None:
    """Exact-name lookup → MFAPI/AMFI scheme_code, or None if not found."""
    with get_session() as session:
        row = session.exec(select(AmfiScheme.scheme_code).where(AmfiScheme.scheme_name == name)).first()
    return str(row) if row else None


def get_scheme_count() -> int:
    """Return total number of schemes in DB."""
    with get_session() as session:
        return int(session.exec(select(func.count()).select_from(AmfiScheme)).one() or 0)


def load_amfi_df() -> pl.DataFrame:
    """Load all AMFI schemes as a polars DataFrame for screener UI."""
    with get_session() as session:
        rows = session.exec(select(AmfiScheme)).all()
    if not rows:
        return pl.DataFrame(
            schema={
                "scheme_code": pl.Int64,
                "scheme_name": pl.Utf8,
                "fund_house": pl.Utf8,
                "category": pl.Utf8,
                "isin_growth": pl.Utf8,
                "nav": pl.Float64,
                "nav_date": pl.Date,
            }
        )
    return pl.DataFrame(
        {
            "scheme_code": [r.scheme_code for r in rows],
            "scheme_name": [r.scheme_name for r in rows],
            "fund_house": [r.fund_house for r in rows],
            "category": [r.category for r in rows],
            "isin_growth": [r.isin_growth for r in rows],
            "nav": [r.nav for r in rows],
            "nav_date": [r.nav_date for r in rows],
        }
    )
