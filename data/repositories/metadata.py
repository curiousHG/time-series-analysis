"""Mutual fund metadata repository — AUM, expense ratio, benchmark, etc."""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime

import polars as pl
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import col, func, select

from core.database import get_session
from core.models import AmfiScheme, MfAmc, MfCategory, MfMetadata
from data.fetchers.mutual_fund import fetch_fund_metadata
from data.repositories.amfi import upsert_amc, upsert_category
from data.repositories.scheme_codes import mint_synthetic_codes

logger = logging.getLogger("data.repositories.metadata")


def _attach_amfi_fields(meta: dict) -> dict:
    """Look up fund_house and category text from amfi_schemes via the dim tables.

    Reads through `fund_house_id` / `category_id` since the legacy text columns on
    `amfi_schemes` were dropped in the Phase 1 normalisation.
    """
    with get_session() as session:
        row = session.exec(
            select(AmfiScheme.fund_house_id, AmfiScheme.category_id).where(
                AmfiScheme.scheme_name == meta["scheme_name"]
            )
        ).first()
        if row is None:
            return meta
        fh_id, cat_id = row
        if fh_id is not None and not meta.get("fund_house"):
            amc = session.exec(select(MfAmc.name).where(MfAmc.id == fh_id)).first()
            if amc is not None:
                meta["fund_house"] = amc[0] if isinstance(amc, tuple) else amc
        if cat_id is not None and not meta.get("category"):
            cat = session.exec(select(MfCategory.name).where(MfCategory.id == cat_id)).first()
            if cat is not None:
                meta["category"] = cat[0] if isinstance(cat, tuple) else cat
    return meta


def save_metadata(meta: dict) -> None:
    meta = dict(meta)
    meta = _attach_amfi_fields(meta)
    meta["fetched_at"] = datetime.now(UTC).replace(tzinfo=None)
    scheme_name = meta.pop("scheme_name")
    with get_session() as session:
        # Phase 2: mf_metadata is keyed on scheme_code, not scheme_name. Resolve via
        # amfi_schemes; if the scheme isn't there yet, mint a synthetic-negative row so
        # the FK doesn't violate. The mint shares this session so it commits atomically
        # with the metadata insert below.
        scheme_code = session.exec(select(AmfiScheme.scheme_code).where(AmfiScheme.scheme_name == scheme_name)).first()
        # session.exec(select(SingleCol)).first() can return either a scalar or a 1-tuple
        # Row depending on the SQLAlchemy code path — unwrap defensively.
        if isinstance(scheme_code, tuple):
            scheme_code = scheme_code[0]
        minted_synthetic = scheme_code is None
        if minted_synthetic:
            scheme_code = mint_synthetic_codes(session, [scheme_name])[scheme_name]
            logger.warning("Assigned synthetic code %d for new metadata scheme %s", scheme_code, scheme_name)
        meta["scheme_code"] = scheme_code
        # Resolve dim FKs: get-or-create rows in mf_amc / mf_category, then drop the text
        # versions before insert — those columns no longer exist on mf_metadata.
        meta["fund_house_id"] = upsert_amc(session, meta.pop("fund_house", None))
        meta["category_id"] = upsert_category(session, meta.pop("category", None))
        stmt = (
            pg_insert(MfMetadata)
            .values(**meta)
            .on_conflict_do_update(
                index_elements=["scheme_code"],
                set_={k: v for k, v in meta.items() if k != "scheme_code"},
            )
        )
        session.exec(stmt)
        session.commit()
    if minted_synthetic:
        from data.repositories.holdings import clear_slug_cache

        clear_slug_cache()
    logger.info("Saved metadata for %s (AUM=%s)", scheme_name, meta.get("aum_crores"))


def load_metadata(scheme_names: list[str] | None = None) -> pl.DataFrame:
    """Read metadata via JOIN through `amfi_schemes` for the scheme_name + dims for AMC /
    category text. Phase 2: mf_metadata is keyed on scheme_code; the JOIN to amfi_schemes
    surfaces scheme_name for callers who still pass names.
    """
    with get_session() as session:
        stmt = (
            select(
                AmfiScheme.scheme_name,
                MfMetadata,
                MfAmc.name.label("amc_name"),
                MfCategory.name.label("category_name"),
            )
            .join(AmfiScheme, MfMetadata.scheme_code == AmfiScheme.scheme_code)
            .join(MfAmc, MfMetadata.fund_house_id == MfAmc.id, isouter=True)
            .join(MfCategory, MfMetadata.category_id == MfCategory.id, isouter=True)
        )
        if scheme_names:
            stmt = stmt.where(col(AmfiScheme.scheme_name).in_(scheme_names))
        rows = session.exec(stmt).all()

    if not rows:
        return pl.DataFrame(schema={"schemeName": pl.Utf8})

    return pl.DataFrame(
        [
            {
                "schemeName": r[0],
                "aumCrores": r[1].aum_crores,
                "aumAsOf": r[1].aum_as_of,
                "expenseRatio": r[1].expense_ratio,
                "expenseRatioAsOf": r[1].expense_ratio_as_of,
                "benchmark": r[1].benchmark,
                "launchDate": r[1].launch_date,
                "category": r[3],  # JOIN'd from mf_category
                "assetClass": r[1].asset_class,
                "status": r[1].status,
                "minInvestment": r[1].min_investment,
                "minTopup": r[1].min_topup,
                "turnoverRatio": r[1].turnover_ratio,
                "exitLoad": r[1].exit_load,
                "fundHouse": r[2],  # JOIN'd from mf_amc
                "fundManager": r[1].fund_manager,
                "sourceUrl": r[1].source_url,
                "fetchedAt": r[1].fetched_at,
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
