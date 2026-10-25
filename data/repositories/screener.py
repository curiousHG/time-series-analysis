"""Unified screener data loader: one SELECT joining AMFI schemes with metadata, metrics, registry and dims."""

from __future__ import annotations

import polars as pl
from sqlalchemy import select as sa_select
from sqlalchemy.orm import aliased
from sqlmodel import col

from core.database import get_session
from core.models import AmfiScheme, MfAmc, MfCategory, MfMetadata, MfRegistry, MfSchemeMetrics
from core.timing import timeit
from data.constants import METRIC_COLS


@timeit("screener.load_screener_view")
def load_screener_view() -> pl.DataFrame:
    """One row per AMFI scheme, LEFT-JOINed with metadata, every metrics column, registry status and dims."""
    amfi_cat = aliased(MfCategory)

    stmt = (
        sa_select(
            AmfiScheme.scheme_code,
            AmfiScheme.scheme_name,
            MfAmc.name.label("fund_house"),
            amfi_cat.name.label("category"),
            col(AmfiScheme.sub_category).label("sub_category"),
            col(AmfiScheme.plan).label("amfi_plan"),
            col(AmfiScheme.option).label("amfi_option"),
            AmfiScheme.isin_growth,
            AmfiScheme.nav,
            AmfiScheme.nav_date,
            MfMetadata.aum_crores,
            MfMetadata.expense_ratio,
            MfMetadata.benchmark,
            MfMetadata.asset_class,
            MfRegistry.nav_status,
            MfRegistry.holdings_status,
            MfRegistry.metadata_status,
            *METRIC_COLS,
        )
        .select_from(AmfiScheme)
        .join(MfAmc, AmfiScheme.fund_house_id == MfAmc.id, isouter=True)
        .join(amfi_cat, AmfiScheme.category_id == amfi_cat.id, isouter=True)
        .join(MfMetadata, MfMetadata.scheme_code == AmfiScheme.scheme_code, isouter=True)
        .join(MfSchemeMetrics, MfSchemeMetrics.scheme_code == AmfiScheme.scheme_code, isouter=True)
        .join(MfRegistry, MfRegistry.scheme_code == AmfiScheme.scheme_code, isouter=True)
    )

    with get_session() as session:
        rows = session.execute(stmt).mappings().all()

    if not rows:
        return pl.DataFrame()
    return pl.from_dicts([dict(r) for r in rows], infer_schema_length=None)
