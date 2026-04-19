"""AMFI master data store — sync, ISIN lookup, name search."""

import logging
from sqlmodel import select, col
from sqlalchemy.dialects.postgresql import insert as pg_insert

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
            session.execute(stmt)
        session.commit()

    logger.info("Synced %d AMFI schemes to database", len(schemes))
    return len(schemes)


def lookup_by_isin(isin: str) -> AmfiScheme | None:
    """Find a scheme by ISIN (growth or reinvestment)."""
    with get_session() as session:
        result = session.execute(
            select(AmfiScheme).where(col(AmfiScheme.isin_growth) == isin)
        ).scalars().first()
        if result:
            return result

        result = session.execute(
            select(AmfiScheme).where(col(AmfiScheme.isin_reinvestment) == isin)
        ).scalars().first()
        return result


def lookup_by_name(query: str) -> list[AmfiScheme]:
    """Search schemes by name (case-insensitive LIKE)."""
    with get_session() as session:
        return (
            session.execute(
                select(AmfiScheme).where(
                    col(AmfiScheme.scheme_name).ilike(f"%{query}%")
                )
            )
            .scalars()
            .all()
        )


def get_scheme_count() -> int:
    """Return total number of schemes in DB."""
    with get_session() as session:
        result = session.execute(select(AmfiScheme)).scalars().all()
        return len(result)
