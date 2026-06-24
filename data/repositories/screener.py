"""Unified screener data loader.

Single SELECT joining the AMFI universe with metadata, cached metrics, registry
status, and the AMC / category dim tables. Replaces the four-helper polars-JOIN
pipeline that previously lived in `services/screener_service.py`.

Why this exists separately from the four piecewise loaders (`load_amfi_df`,
`load_metadata_all`, `load_cached_metrics`, `list_tracked`): those are reused by
other pages with their own tighter requirements. This one is screener-shaped — it
returns one row per AMFI scheme with every column the screener UI needs, in the
exact column names that `services.screener_service.apply_filters` expects.
"""

from __future__ import annotations

import polars as pl
from sqlalchemy import func
from sqlalchemy import select as sa_select
from sqlalchemy.orm import aliased

from core.database import get_session
from core.models import AmfiScheme, MfAmc, MfCategory, MfMetadata, MfRegistry, MfSchemeMetrics
from core.timing import timeit
from data.constants import METRIC_COLS


@timeit("screener.load_screener_view")
def load_screener_view() -> pl.DataFrame:
    """Run the screener's master JOIN as a single SELECT.

    Output: one row per AMFI scheme, LEFT-JOINed with metadata / metrics / registry /
    dim tables. Columns:
      * AMFI: scheme_code, scheme_name, isin_growth, nav, nav_date
      * Dims: fund_house (from mf_amc), category (metadata's preferred over AMFI's)
      * Metadata: aum_crores, expense_ratio, benchmark, asset_class
      * Registry: nav_status, holdings_status, metadata_status
      * Metrics: every `mf_scheme_metrics` column (cagr_*, sharpe_*, pct_*, ...)
    """
    # `mf_category` joined twice — once via amfi.category_id, once via metadata.category_id.
    amfi_cat = aliased(MfCategory)
    meta_cat = aliased(MfCategory)

    stmt = (
        sa_select(
            AmfiScheme.scheme_code,
            AmfiScheme.scheme_name,
            MfAmc.name.label("fund_house"),
            func.coalesce(meta_cat.name, amfi_cat.name).label("category"),
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
        .join(meta_cat, MfMetadata.category_id == meta_cat.id, isouter=True)
        .join(MfSchemeMetrics, MfSchemeMetrics.scheme_code == AmfiScheme.scheme_code, isouter=True)
        .join(MfRegistry, MfRegistry.scheme_code == AmfiScheme.scheme_code, isouter=True)
    )

    with get_session() as session:
        rows = session.execute(stmt).mappings().all()

    if not rows:
        return pl.DataFrame()
    return pl.from_dicts([dict(r) for r in rows])
