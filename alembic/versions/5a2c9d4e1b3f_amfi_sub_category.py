"""amfi_schemes: add sub_category column

Revision ID: 5a2c9d4e1b3f
Revises: 47b1f0a9c3d2
Create Date: 2026-06-27 00:00:00.000000+00:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "5a2c9d4e1b3f"
down_revision = "47b1f0a9c3d2"
branch_labels = None
depends_on = None


def _has_col() -> bool:
    return any(c["name"] == "sub_category" for c in sa.inspect(op.get_bind()).get_columns("amfi_schemes"))


def upgrade() -> None:
    if not _has_col():
        op.add_column("amfi_schemes", sa.Column("sub_category", sa.String(), nullable=True))
        op.create_index("ix_amfi_schemes_sub_category", "amfi_schemes", ["sub_category"], unique=False)


def downgrade() -> None:
    if _has_col():
        op.drop_index("ix_amfi_schemes_sub_category", table_name="amfi_schemes")
        op.drop_column("amfi_schemes", "sub_category")
