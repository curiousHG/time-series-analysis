"""Mutual fund metadata repository — AUM, expense ratio, benchmark, etc."""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime

import polars as pl
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import col, func, select

from core.database import get_session
from core.models import AmfiScheme, MfAmc, MfCategory, MfMetadata
from data.exceptions import SourceNotFound
from data.fetchers.advisorkhoj import fetch_fund_metadata
from data.fetchers.kuvera import fetch_fund_metadata_kuvera
from data.fetchers.schemas import FundMetadata
from data.repositories.advisorkhoj_keys import resolve_advisorkhoj_slug
from data.repositories.amfi import find_direct_sibling, get_scheme_details_by_name, upsert_amc, upsert_category
from data.repositories.holdings import clear_slug_cache
from data.repositories.scheme_codes import mint_synthetic_codes

logger = logging.getLogger("data.repositories.metadata")


def _attach_amfi_fields(meta: FundMetadata) -> FundMetadata:
    """Fill missing fund_house / category text from amfi_schemes via the dim tables."""
    update: dict[str, str] = {}
    with get_session() as session:
        row = session.exec(
            select(AmfiScheme.fund_house_id, AmfiScheme.category_id).where(AmfiScheme.scheme_name == meta.scheme_name)
        ).first()
        if row is None:
            return meta
        fh_id, cat_id = row
        if fh_id is not None and not meta.fund_house:
            amc = session.exec(select(MfAmc.name).where(MfAmc.id == fh_id)).first()
            if amc is not None:
                update["fund_house"] = amc[0] if isinstance(amc, tuple) else amc
        if cat_id is not None and not meta.category:
            cat = session.exec(select(MfCategory.name).where(MfCategory.id == cat_id)).first()
            if cat is not None:
                update["category"] = cat[0] if isinstance(cat, tuple) else cat
    return meta.model_copy(update=update)


def save_metadata(metadata: FundMetadata) -> None:
    meta = _attach_amfi_fields(metadata).model_dump(exclude_unset=True)
    meta["fetched_at"] = datetime.now(UTC).replace(tzinfo=None)
    scheme_name = meta.pop("scheme_name")
    with get_session() as session:
        scheme_code = session.exec(select(AmfiScheme.scheme_code).where(AmfiScheme.scheme_name == scheme_name)).first()
        if isinstance(scheme_code, tuple):
            scheme_code = scheme_code[0]
        minted_synthetic = scheme_code is None
        if minted_synthetic:
            scheme_code = mint_synthetic_codes(session, [scheme_name])[scheme_name]
            logger.warning("Assigned synthetic code %d for new metadata scheme %s", scheme_code, scheme_name)
        meta["scheme_code"] = scheme_code
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
        clear_slug_cache()
    logger.info("Saved metadata for %s (AUM=%s)", scheme_name, meta.get("aum_crores"))


def load_metadata(scheme_names: list[str] | None = None) -> pl.DataFrame:
    """Metadata for the given schemes (or all), with scheme name, AMC and category joined in."""
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
                "category": r[3],
                "assetClass": r[1].asset_class,
                "status": r[1].status,
                "minInvestment": r[1].min_investment,
                "minTopup": r[1].min_topup,
                "turnoverRatio": r[1].turnover_ratio,
                "exitLoad": r[1].exit_load,
                "investmentObjective": r[1].investment_objective,
                "riskLevel": r[1].risk_level,
                "fundHouse": r[2],
                "fundManager": r[1].fund_manager,
                "sourceUrl": r[1].source_url,
                "fetchedAt": r[1].fetched_at,
            }
            for r in rows
        ]
    )


def _advisorkhoj_metadata(scheme_name: str, scheme_code: int | None) -> tuple[FundMetadata | None, Exception | None]:
    """AdvisorKhoj scrape by verified slug first, then by name. Returns (metadata, last_error)."""
    keys: list[str | None] = []
    if scheme_code is not None:
        keys.append(resolve_advisorkhoj_slug(scheme_code))
    keys.append(None)
    last_error: Exception | None = None
    for key in keys:
        if key is None and keys[0] is not None and keys[0] == scheme_name:
            continue
        try:
            meta = fetch_fund_metadata(scheme_name, lookup_key=key)
        except SourceNotFound as e:
            last_error = e
            continue
        except Exception as e:
            last_error = e
            logger.warning("AdvisorKhoj metadata failed for %s (key=%s): %s", scheme_name, key, e)
            continue
        if any(getattr(meta, k) is not None for k in ("aum_crores", "expense_ratio", "launch_date")):
            return meta, None
    return None, last_error


def refresh_metadata(scheme_name: str) -> FundMetadata:
    """Fetch metadata for one scheme (Kuvera, then AdvisorKhoj by slug and by name, then a Direct sibling) and save it,
    replacing any existing row.

    Returns the saved metadata; raises the last source error when nothing is found.
    """
    amfi_row = get_scheme_details_by_name(scheme_name)
    isins = tuple((amfi_row.get("isin_growth"), amfi_row.get("isin_reinvestment")) if amfi_row else ())
    scheme_code = amfi_row.get("scheme_code") if amfi_row else None

    meta = fetch_fund_metadata_kuvera(scheme_name, isins)
    source = "Kuvera" if meta else None

    adv, adv_error = _advisorkhoj_metadata(scheme_name, scheme_code)
    if meta is None and adv is not None:
        meta, source = adv, "AdvisorKhoj"
    elif meta is not None and adv is not None:
        meta = meta.model_copy(
            update={k: v for k, v in adv.model_dump().items() if v is not None and getattr(meta, k) is None}
        )

    if meta is None and scheme_code is not None:
        meta = _from_direct_sibling(scheme_name, scheme_code)
        source = "Kuvera (Direct-plan sibling, scheme-level fields only)" if meta else None

    if meta is None:
        raise adv_error or ValueError(f"No metadata found on any source for {scheme_name}")
    logger.info("Metadata for %s via %s", scheme_name, source)
    save_metadata(meta)
    return meta


_SCHEME_LEVEL_FIELDS = (
    "aum_crores",
    "category",
    "asset_class",
    "status",
    "turnover_ratio",
    "investment_objective",
    "risk_level",
    "fund_manager",
)


def _from_direct_sibling(scheme_name: str, scheme_code: int) -> FundMetadata | None:
    """Scheme-level fields from the Direct twin's Kuvera record, for a plan no source knows."""
    sibling = find_direct_sibling(scheme_code)
    if sibling is None:
        return None
    twin = fetch_fund_metadata_kuvera(sibling["scheme_name"], (sibling["isin_growth"], sibling["isin_reinvestment"]))
    if not twin or twin.aum_crores is None:
        return None
    fields = dict.fromkeys(FundMetadata.model_fields)
    fields.update({k: getattr(twin, k) for k in _SCHEME_LEVEL_FIELDS})
    fields.update(scheme_name=scheme_name, source_url=twin.source_url)
    return FundMetadata(**fields)


def ensure_metadata(scheme_names: list[str]) -> pl.DataFrame:
    """DB-first: load existing rows, fetch only those missing an AUM in parallel, return all."""
    if not scheme_names:
        return pl.DataFrame(schema={"schemeName": pl.Utf8})

    existing = load_metadata(scheme_names)
    have = set(existing.filter(pl.col("aumCrores").is_not_null())["schemeName"].to_list()) if existing.height else set()
    missing = [n for n in scheme_names if n not in have]

    if missing:
        logger.info("Fetching metadata for %d missing schemes", len(missing))
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(refresh_metadata, n): n for n in missing}
            for future in as_completed(futures):
                name = futures[future]
                try:
                    future.result()
                except Exception as e:
                    logger.error("Failed to fetch metadata for %s: %s", name, e)

    return load_metadata(scheme_names)


def count_metadata() -> int:
    """Total number of mf_metadata rows."""
    with get_session() as session:
        return int(session.exec(select(func.count()).select_from(MfMetadata)).one() or 0)


def load_metadata_all() -> pl.DataFrame:
    """Load every mf_metadata row."""
    return load_metadata(None)
