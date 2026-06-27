"""stock fundamentals: stock_registry delta columns

Revision ID: 366acc72ee77
Revises: 20260622_0002
Create Date: 2026-06-24 14:10:40.335968+00:00

Per project convention (CLAUDE.md): `create_all` creates the NEW tables (stock_quarterly,
stock_metrics) on boot; Alembic owns only deltas. So this migration just adds the new
columns to the existing stock_registry table.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "366acc72ee77"
down_revision = "20260622_0002"
branch_labels = None
depends_on = None

_COLS = {
    "isin": sa.String(),
    "series": sa.String(),
    "fundamentals_status": sa.String(),
    "fundamentals_as_of": sa.DateTime(),
}


def _existing() -> set[str]:
    bind = op.get_bind()
    return {c["name"] for c in sa.inspect(bind).get_columns("stock_registry")}


def upgrade() -> None:
    have = _existing()
    for name, type_ in _COLS.items():
        if name not in have:
            op.add_column("stock_registry", sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    have = _existing()
    for name in reversed(list(_COLS)):
        if name in have:
            op.drop_column("stock_registry", name)
