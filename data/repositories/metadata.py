"""Mutual fund metadata repository — AUM, expense ratio, benchmark, etc."""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import polars as pl
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import col, func, select

from core.database import get_session
from core.models import AmfiScheme, MfMetadata
from data.fetchers.mutual_fund import fetch_fund_metadata

logger = logging.getLogger("data.repositories.metadata")


def _attach_amfi_fields(meta: dict) -> dict:
    """Look up fund_house and (fallback) category from amfi_schemes by exact name."""
    with get_session() as session:
        row = session.exec(select(AmfiScheme).where(AmfiScheme.scheme_name == meta["scheme_name"])).first()
    if row is not None:
        meta["fund_house"] = row.fund_house
        if not meta.get("category"):
            meta["category"] = row.category
    return meta


def save_metadata(meta: dict) -> None:
    meta = dict(meta)
    meta = _attach_amfi_fields(meta)
    meta["fetched_at"] = datetime.utcnow()
    with get_session() as session:
        stmt = (
            pg_insert(MfMetadata)
            .values(**meta)
            .on_conflict_do_update(
                index_elements=["scheme_name"],
                set_={k: v for k, v in meta.items() if k != "scheme_name"},
            )
        )
        session.exec(stmt)
        session.commit()
    logger.info("Saved metadata for %s (AUM=%s)", meta["scheme_name"], meta.get("aum_crores"))


def load_metadata(scheme_names: list[str] | None = None) -> pl.DataFrame:
    with get_session() as session:
        stmt = select(MfMetadata)
        if scheme_names:
            stmt = stmt.where(col(MfMetadata.scheme_name).in_(scheme_names))
        rows = session.exec(stmt).all()

    if not rows:
        return pl.DataFrame(schema={"schemeName": pl.Utf8})

    return pl.DataFrame(
        [
            {
                "schemeName": r.scheme_name,
                "aumCrores": r.aum_crores,
                "aumAsOf": r.aum_as_of,
                "expenseRatio": r.expense_ratio,
                "expenseRatioAsOf": r.expense_ratio_as_of,
                "benchmark": r.benchmark,
                "launchDate": r.launch_date,
                "category": r.category,
                "assetClass": r.asset_class,
                "status": r.status,
                "minInvestment": r.min_investment,
                "minTopup": r.min_topup,
                "turnoverRatio": r.turnover_ratio,
                "exitLoad": r.exit_load,
                "fundHouse": r.fund_house,
                "fundManager": r.fund_manager,
                "sourceUrl": r.source_url,
                "fetchedAt": r.fetched_at,
            }
            for r in rows
        ]
    )


def fetch_and_save(scheme_name: str) -> dict:
    """Fetch metadata for one scheme and persist it. Returns the saved dict."""
    meta = fetch_fund_metadata(scheme_name)
    save_metadata(meta)
    return meta


def ensure_metadata(scheme_names: list[str]) -> pl.DataFrame:
    """DB-first: load existing rows, fetch only the missing ones in parallel, return all."""
    if not scheme_names:
        return pl.DataFrame(schema={"schemeName": pl.Utf8})

    existing = load_metadata(scheme_names)
    have = set(existing["schemeName"].to_list()) if existing.height else set()
    missing = [n for n in scheme_names if n not in have]

    if missing:
        logger.info("Fetching metadata for %d missing schemes", len(missing))
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(fetch_and_save, n): n for n in missing}
            for future in as_completed(futures):
                name = futures[future]
                try:
                    future.result()
                except Exception as e:
                    logger.error("Failed to fetch metadata for %s: %s", name, e)

    return load_metadata(scheme_names)


def refresh_metadata(scheme_name: str) -> dict:
    """Force re-fetch of one scheme's metadata, replacing the existing row."""
    return fetch_and_save(scheme_name)


def count_metadata() -> int:
    """Return total number of mf_metadata rows."""
    with get_session() as session:
        return int(session.exec(select(func.count()).select_from(MfMetadata)).one() or 0)


def load_metadata_all() -> pl.DataFrame:
    """Load every mf_metadata row."""
    return load_metadata(None)
