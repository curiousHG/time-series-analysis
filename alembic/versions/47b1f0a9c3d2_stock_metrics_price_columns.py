"""stock_metrics: price-derived CAPM columns (return/vol/beta/alpha/r2)

Revision ID: 47b1f0a9c3d2
Revises: 366acc72ee77
Create Date: 2026-06-25 00:00:00.000000+00:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "47b1f0a9c3d2"
down_revision = "366acc72ee77"
branch_labels = None
depends_on = None

_COLS = ("return_1y", "vol_1y", "beta_1y", "alpha_1y", "r2_1y")


def _existing() -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns("stock_metrics")}


def upgrade() -> None:
    have = _existing()
    for name in _COLS:
        if name not in have:
            op.add_column("stock_metrics", sa.Column(name, sa.Float(), nullable=True))


def downgrade() -> None:
    have = _existing()
    for name in reversed(_COLS):
        if name in have:
            op.drop_column("stock_metrics", name)
