"""AMFI master data store — sync, ISIN lookup, name search.

Process-level caches:
- `_amc_cache`, `_category_cache`: dim-name → id, write-through via `upsert_amc` /
  `upsert_category`; refreshed at the start of every `sync_amfi_master`.
- Any code path inserting into `amfi_schemes` must call `clear_slug_cache`
  (holdings.py) after commit, or the LRU slug→code map misses the new scheme until
  process restart.
"""

import logging
from datetime import UTC, datetime

import polars as pl
from sqlalchemy import select as sa_select
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import col, func, select

from core.database import get_session
from core.models import AmfiScheme, MfAmc, MfCategory
from data.fetchers.mutual_fund import fetch_amfi_master
from data.repositories.holdings import clear_slug_cache

logger = logging.getLogger("data.store.amfi")


# In-process dim-table cache. AMCs/categories are tiny enums; write-through avoids
# 14K+ dim INSERTs during a full AMFI sync.
_amc_cache: dict[str, int] = {}
_category_cache: dict[str, int] = {}


def _refresh_dim_caches(session) -> None:
    """Pre-load every (name → id) pair for both dim tables."""
    _amc_cache.clear()
    _category_cache.clear()
    for r in session.exec(select(MfAmc.id, MfAmc.name)).all():
        _amc_cache[r[1]] = r[0]
    for r in session.exec(select(MfCategory.id, MfCategory.name)).all():
        _category_cache[r[1]] = r[0]


def _unwrap_id(row) -> int | None:
    """Coerce a `.first()` result (scalar or 1-tuple Row) to a plain int.

    SQLAlchemy ≥ 2.0 returns non-tuple `Row` objects from `.returning(col)`, so a plain
    `isinstance(row, tuple)` check misses them.
    """
    if row is None:
        return None
    if isinstance(row, int):
        return row
    try:
        return int(row[0])
    except (TypeError, IndexError):
        return int(row)


def upsert_amc(session, name: str | None) -> int | None:
    """Get-or-create AMC. Returns mf_amc.id (or None when name is empty)."""
    if not name:
        return None
    cached = _amc_cache.get(name)
    if cached is not None:
        return cached
    row = session.exec(
        pg_insert(MfAmc)
        .values(name=name)
        .on_conflict_do_update(index_elements=["name"], set_={"name": name})
        .returning(MfAmc.id)
    ).first()
    new_id = _unwrap_id(row)
    _amc_cache[name] = new_id
    return new_id


def upsert_category(session, name: str | None) -> int | None:
    """Get-or-create category. Returns mf_category.id (or None when name is empty)."""
    if not name:
        return None
    cached = _category_cache.get(name)
    if cached is not None:
        return cached
    row = session.exec(
        pg_insert(MfCategory)
        .values(name=name)
        .on_conflict_do_update(index_elements=["name"], set_={"name": name})
        .returning(MfCategory.id)
    ).first()
    new_id = _unwrap_id(row)
    _category_cache[name] = new_id
    return new_id


def _prepare_sync_row(
    scheme: dict,
    *,
    existing_codes: set[int],
    fund_house_id: int | None,
    category_id: int | None,
    synced_at: datetime,
) -> dict:
    """Shape one AMFI sync row for insert/update.

    `db_added_at` ("inserted into our local DB") is set only for new codes; conflict
    updates preserve the existing value.
    """
    row = dict(scheme)
    code = int(row["scheme_code"])
    row.pop("fund_house", None)
    row.pop("category", None)
    row["fund_house_id"] = fund_house_id
    row["category_id"] = category_id
    if code not in existing_codes:
        row["db_added_at"] = synced_at
    return row


def sync_amfi_master() -> int:
    """Fetch AMFI NAVAll.txt and upsert all schemes into DB. Returns count.

    `fund_house` / `category` text is resolved to dim-table ids (mf_amc, mf_category) and
    stripped from the row — the text columns were dropped in Phase 1 normalisation.
    """
    schemes = fetch_amfi_master()
    synced_at = datetime.now(UTC).replace(tzinfo=None)

    with get_session() as session:
        _refresh_dim_caches(session)
        existing_codes = set(session.exec(select(AmfiScheme.scheme_code)).all())
        for s in schemes:
            fund_house_id = upsert_amc(session, s.get("fund_house"))
            category_id = upsert_category(session, s.get("category"))
            row = _prepare_sync_row(
                s,
                existing_codes=existing_codes,
                fund_house_id=fund_house_id,
                category_id=category_id,
                synced_at=synced_at,
            )
            stmt = (
                pg_insert(AmfiScheme)
                .values(**row)
                .on_conflict_do_update(
                    index_elements=["scheme_code"],
                    set_={k: v for k, v in row.items() if k not in ("scheme_code", "db_added_at")},
                )
            )
            session.exec(stmt)
        session.commit()

    # New schemes invalidate the slug→code map cached in holdings.py.
    clear_slug_cache()
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
    """Fuzzy-search AMFI schemes by name via pg_trgm similarity, ILIKE fallback.

    Columns: schemeName, schemeCode, fundHouse, category, isinGrowth, score (0-1).
    """
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
                    SELECT s.scheme_name, s.scheme_code, a.name AS fund_house, c.name AS category,
                           s.isin_growth, similarity(s.scheme_name, :q) AS score
                    FROM amfi_schemes s
                    LEFT JOIN mf_amc a ON s.fund_house_id = a.id
                    LEFT JOIN mf_category c ON s.category_id = c.id
                    WHERE s.scheme_name % :q OR s.scheme_name ILIKE :pattern
                    ORDER BY score DESC, length(s.scheme_name) ASC
                    LIMIT :limit
                    """
                ).bindparams(q=q, pattern=f"%{q}%", limit=limit)
            ).all()
        except Exception:
            # pg_trgm unavailable — plain ILIKE fallback
            rows = session.exec(
                sql_text(
                    """
                    SELECT s.scheme_name, s.scheme_code, a.name AS fund_house, c.name AS category,
                           s.isin_growth, 0.0 AS score
                    FROM amfi_schemes s
                    LEFT JOIN mf_amc a ON s.fund_house_id = a.id
                    LEFT JOIN mf_category c ON s.category_id = c.id
                    WHERE s.scheme_name ILIKE :pattern
                    ORDER BY length(s.scheme_name) ASC
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


def get_scheme_details_by_name(scheme_name: str) -> dict | None:
    """One scheme's AMFI master + dim text as a flat dict (or None if name not found).

    Returns a plain dict so the MF Analysis header UI needn't import ORM classes.
    """
    with get_session() as session:
        row = session.execute(
            sa_select(
                col(AmfiScheme.scheme_code),
                col(AmfiScheme.scheme_name),
                col(AmfiScheme.isin_growth),
                col(AmfiScheme.isin_reinvestment),
                col(AmfiScheme.nav),
                col(AmfiScheme.nav_date),
                col(MfAmc.name).label("fund_house"),
                col(MfCategory.name).label("category"),
            )
            .join(MfAmc, col(AmfiScheme.fund_house_id) == col(MfAmc.id), isouter=True)
            .join(MfCategory, col(AmfiScheme.category_id) == col(MfCategory.id), isouter=True)
            .where(col(AmfiScheme.scheme_name) == scheme_name)
        ).first()
    if row is None:
        return None
    return {
        "scheme_code": row[0],
        "scheme_name": row[1],
        "isin_growth": row[2],
        "isin_reinvestment": row[3],
        "nav": row[4],
        "nav_date": row[5],
        "fund_house": row[6],
        "category": row[7],
    }


def get_scheme_count() -> int:
    """Return total number of schemes in DB."""
    with get_session() as session:
        return int(session.exec(select(func.count()).select_from(AmfiScheme)).one() or 0)


def load_recent_additions(limit: int = 25) -> pl.DataFrame:
    """Recent AMFI schemes added to the local database."""
    with get_session() as session:
        rows = session.execute(
            sa_select(
                col(AmfiScheme.scheme_code),
                col(AmfiScheme.scheme_name),
                col(MfAmc.name).label("fund_house"),
                col(MfCategory.name).label("category"),
                col(AmfiScheme.isin_growth),
                col(AmfiScheme.db_added_at),
            )
            .join(MfAmc, col(AmfiScheme.fund_house_id) == col(MfAmc.id), isouter=True)
            .join(MfCategory, col(AmfiScheme.category_id) == col(MfCategory.id), isouter=True)
            .where(col(AmfiScheme.db_added_at).is_not(None))
            .order_by(col(AmfiScheme.db_added_at).desc(), col(AmfiScheme.scheme_code).desc())
            .limit(limit)
        ).all()
    schema = {
        "schemeCode": pl.Int64,
        "schemeName": pl.Utf8,
        "fundHouse": pl.Utf8,
        "category": pl.Utf8,
        "isinGrowth": pl.Utf8,
        "dbAddedAt": pl.Datetime,
    }
    if not rows:
        return pl.DataFrame(schema=schema)
    cols = list(schema)
    return pl.DataFrame({c: [r[i] for r in rows] for i, c in enumerate(cols)}, schema=schema)


def load_amfi_df() -> pl.DataFrame:
    """Load all AMFI schemes as a polars DataFrame for the screener UI.

    `fund_house` / `category` come via JOIN through the dim tables (forward-compatible
    once the legacy text columns are dropped).
    """
    with get_session() as session:
        rows = session.execute(
            sa_select(
                col(AmfiScheme.scheme_code),
                col(AmfiScheme.scheme_name),
                col(MfAmc.name).label("fund_house"),
                col(MfCategory.name).label("category"),
                col(AmfiScheme.isin_growth),
                col(AmfiScheme.nav),
                col(AmfiScheme.nav_date),
            )
            .join(MfAmc, col(AmfiScheme.fund_house_id) == col(MfAmc.id), isouter=True)
            .join(MfCategory, col(AmfiScheme.category_id) == col(MfCategory.id), isouter=True)
        ).all()
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
            "scheme_code": [r[0] for r in rows],
            "scheme_name": [r[1] for r in rows],
            "fund_house": [r[2] for r in rows],
            "category": [r[3] for r in rows],
            "isin_growth": [r[4] for r in rows],
            "nav": [r[5] for r in rows],
            "nav_date": [r[6] for r in rows],
        }
    )
