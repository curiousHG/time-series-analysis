"""port hand-written migrations: metrics columns, volume bigint, pg_trgm

Folds the three steps that used to live in migrations/runner.py into Alembic, so
Alembic is the single owner of schema deltas. Every step is idempotent
(IF NOT EXISTS / guarded), so on a fresh DB — where create_all already built the
full schema from the models — this revision is a no-op. It only does real work on
older DBs that predate the columns / the BIGINT widening / the trigram index.

Revision ID: 20260622_0002
Revises: 20260613_0001
Create Date: 2026-06-22 00:00:00.000000
"""

from __future__ import annotations

import logging

from alembic import op

revision = "20260622_0002"
down_revision = "20260613_0001"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.port_handwritten_migrations")


# Columns that accreted on mf_scheme_metrics after the table was first created.
# create_all never ALTERs an existing table, so older DBs need these added explicitly.
_METRICS_COLUMNS = (
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


def _add_missing_metrics_columns() -> None:
    for col_def in _METRICS_COLUMNS:
        op.execute(f"ALTER TABLE mf_scheme_metrics ADD COLUMN IF NOT EXISTS {col_def}")


def _widen_stock_volume_to_bigint() -> None:
    """INTEGER -> BIGINT (index volumes overflow 32-bit). Only ALTER when actually
    INTEGER — running ALTER COLUMN TYPE on an already-BIGINT column forces a needless
    full-table rewrite. Widening preserves all existing values."""
    conn = op.get_bind()
    current = conn.exec_driver_sql(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_name = 'stock_ohlcv' AND column_name = 'volume'"
    ).first()
    if current is None or current[0] == "bigint":
        return
    op.execute("ALTER TABLE stock_ohlcv ALTER COLUMN volume TYPE BIGINT")


def _install_pg_trgm_index() -> None:
    """Fuzzy scheme-name search uses similarity() from pg_trgm + a GIN index. Run it in
    a SAVEPOINT so a role without CREATE EXTENSION privilege degrades gracefully (search
    falls back to ILIKE — see data/repositories/amfi.py:search_amfi) without poisoning
    the migration transaction."""
    conn = op.get_bind()
    try:
        with conn.begin_nested():
            conn.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS pg_trgm")
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS idx_amfi_scheme_name_trgm "
                "ON amfi_schemes USING gin (scheme_name gin_trgm_ops)"
            )
    except Exception as e:
        logger.warning("pg_trgm setup skipped (search falls back to ILIKE): %s", e)


def upgrade() -> None:
    _add_missing_metrics_columns()
    _widen_stock_volume_to_bigint()
    _install_pg_trgm_index()


def downgrade() -> None:
    # Non-destructive by design: dropping the metrics columns or narrowing volume back
    # to INTEGER would lose data, and both are owned by the SQLModel models anyway. Only
    # the (cheaply rebuildable) GIN index is dropped here.
    op.execute("DROP INDEX IF EXISTS idx_amfi_scheme_name_trgm")
