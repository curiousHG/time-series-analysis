"""Migration runner — idempotent, safe to re-run."""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlmodel import SQLModel

from core.database import engine
from core.timing import timed

logger = logging.getLogger("migrations.runner")


# ---- Migrations -------------------------------------------------------------------------


def _add_missing_metrics_columns(conn) -> None:
    """`mf_scheme_metrics` accumulates columns over time. `create_all` doesn't ALTER existing
    tables, so columns added after the table was first created have to be ADDed manually.
    `ADD COLUMN IF NOT EXISTS` is a no-op on schemas that already have them.
    """
    cols = (
        # Portfolio-style 1Y metrics
        "calmar_1y DOUBLE PRECISION",
        "gain_to_pain_1y DOUBLE PRECISION",
        "cumulative_return_1y DOUBLE PRECISION",
        "avg_daily_return_1y DOUBLE PRECISION",
        "win_rate_1y DOUBLE PRECISION",
        "best_day_1y DOUBLE PRECISION",
        "worst_day_1y DOUBLE PRECISION",
        "var_95_1y DOUBLE PRECISION",
        "cvar_95_1y DOUBLE PRECISION",
        "skew_1y DOUBLE PRECISION",
        "kurt_1y DOUBLE PRECISION",
        "kelly_1y DOUBLE PRECISION",
        "avg_win_1y DOUBLE PRECISION",
        "avg_loss_1y DOUBLE PRECISION",
        "payoff_ratio_1y DOUBLE PRECISION",
        # Rolling-CAGR distribution
        "rolling_1y_min DOUBLE PRECISION",
        "rolling_1y_median DOUBLE PRECISION",
        "rolling_1y_mean DOUBLE PRECISION",
        "rolling_1y_max DOUBLE PRECISION",
        "rolling_3y_min DOUBLE PRECISION",
        "rolling_3y_median DOUBLE PRECISION",
        "rolling_3y_mean DOUBLE PRECISION",
        "rolling_3y_max DOUBLE PRECISION",
        "rolling_5y_min DOUBLE PRECISION",
        "rolling_5y_median DOUBLE PRECISION",
        "rolling_5y_mean DOUBLE PRECISION",
        "rolling_5y_max DOUBLE PRECISION",
        # Phase 1 additions
        "abs_return_3m DOUBLE PRECISION",
        "abs_return_6m DOUBLE PRECISION",
        "abs_return_1y DOUBLE PRECISION",
        "pct_equity DOUBLE PRECISION",
        "pct_debt DOUBLE PRECISION",
        "pct_cash DOUBLE PRECISION",
        "pct_top3 DOUBLE PRECISION",
        "pct_top5 DOUBLE PRECISION",
        "pct_top10 DOUBLE PRECISION",
        "alpha_1y DOUBLE PRECISION",
        "beta_1y DOUBLE PRECISION",
        "r2_1y DOUBLE PRECISION",
        "tracking_error_1y DOUBLE PRECISION",
        "inception_date DATE",
        "downside_vol_1y DOUBLE PRECISION",
    )
    for col_def in cols:
        conn.execute(text(f"ALTER TABLE mf_scheme_metrics ADD COLUMN IF NOT EXISTS {col_def}"))


def _widen_stock_volume_to_bigint(conn) -> None:
    """Index volumes overflow 32-bit INTEGER (~2.1B max). Convert `stock_ohlcv.volume`
    to BIGINT, but only when it's currently INTEGER — running ALTER COLUMN TYPE on an
    already-BIGINT column would force a needless full-table rewrite.
    """
    current = conn.execute(
        text(
            """
            SELECT data_type FROM information_schema.columns
            WHERE table_name = 'stock_ohlcv' AND column_name = 'volume'
            """
        )
    ).first()
    if current is None:
        return  # table not present yet
    data_type = current[0]
    if data_type == "bigint":
        return
    conn.execute(text("ALTER TABLE stock_ohlcv ALTER COLUMN volume TYPE BIGINT"))


def _install_pg_trgm_index(conn) -> None:
    """Fuzzy scheme-name search uses `similarity()` from pg_trgm + a GIN index.
    Falls back silently if the role can't `CREATE EXTENSION` (search will degrade
    to ILIKE — see `data/repositories/amfi.py:search_amfi`).
    """
    try:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_amfi_scheme_name_trgm "
                "ON amfi_schemes USING gin (scheme_name gin_trgm_ops)"
            )
        )
    except Exception as e:
        logger.warning("pg_trgm setup skipped: %s", e)


# ---- Orchestrator -----------------------------------------------------------------------


def run_all() -> None:
    """Run every migration in order. Imports all models first so `create_all` can fill
    in any missing tables before the migrations layer on top.
    """
    with timed("migrations.import_models"):
        import core.models  # noqa: F401  — registers SQLModel metadata

    with timed("migrations.create_all"):
        SQLModel.metadata.create_all(engine)

    with timed("migrations.run_all"), engine.begin() as conn:
        _add_missing_metrics_columns(conn)
        _widen_stock_volume_to_bigint(conn)
        _install_pg_trgm_index(conn)

    logger.info("Migrations complete")
