"""bhavcopy enrichment: turnover/num_trades on stock_ohlcv, turnover_cr/pe/pb/div_yield on index_ohlcv

Persist the columns the NSE bhavcopy already carries but we were discarding: per-stock traded value
and trade count, and per-index turnover + valuation ratios (P/E, P/B, Div Yield). Additive + idempotent
(ADD COLUMN IF NOT EXISTS), so it's a no-op on a fresh DB where create_all already built the columns from
the models, and safe to re-run. History is filled by re-running the bhavcopy backfill (upsert).

Revision ID: d1e4b7c2a9f8
Revises: c3f8a1d0b9e2
Create Date: 2026-07-05 00:00:00.000000
"""

from __future__ import annotations

from alembic import op

revision = "d1e4b7c2a9f8"
down_revision = "c3f8a1d0b9e2"
branch_labels = None
depends_on = None

_STOCK_COLUMNS = (
    "turnover DOUBLE PRECISION",
    "num_trades BIGINT",
)
_INDEX_COLUMNS = (
    "turnover_cr DOUBLE PRECISION",
    "pe DOUBLE PRECISION",
    "pb DOUBLE PRECISION",
    "div_yield DOUBLE PRECISION",
)


def upgrade() -> None:
    for col_def in _STOCK_COLUMNS:
        op.execute(f"ALTER TABLE stock_ohlcv ADD COLUMN IF NOT EXISTS {col_def}")
    for col_def in _INDEX_COLUMNS:
        op.execute(f"ALTER TABLE index_ohlcv ADD COLUMN IF NOT EXISTS {col_def}")


def downgrade() -> None:
    for name in ("turnover", "num_trades"):
        op.execute(f"ALTER TABLE stock_ohlcv DROP COLUMN IF EXISTS {name}")
    for name in ("turnover_cr", "pe", "pb", "div_yield"):
        op.execute(f"ALTER TABLE index_ohlcv DROP COLUMN IF EXISTS {name}")
