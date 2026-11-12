"""Add mf_aaum (AMFI average AUM per scheme per period) and index_tri (NSE total-return indices).

Revision ID: h9c5e8f1a2b4
Revises: a4f9c2d7e1b6
Create Date: 2026-11-13 02:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "h9c5e8f1a2b4"
down_revision = "a4f9c2d7e1b6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mf_aaum",
        sa.Column("scheme_code", sa.Integer(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("scheme_name", sa.String(), nullable=True),
        sa.Column("aaum_lakhs", sa.Float(), nullable=False),
        sa.Column("fof_domestic_lakhs", sa.Float(), nullable=False, server_default="0"),
        sa.Column("category", sa.String(), nullable=True),
        sa.Column("fund_house", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("scheme_code", "period_end"),
        if_not_exists=True,
    )
    op.create_index("ix_mf_aaum_period_end", "mf_aaum", ["period_end"], if_not_exists=True)
    op.create_table(
        "index_tri",
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("tri", sa.Float(), nullable=False),
        sa.Column("ntr", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("date", "symbol"),
        if_not_exists=True,
    )
    op.create_index("ix_index_tri_symbol", "index_tri", ["symbol"], if_not_exists=True)


def downgrade() -> None:
    op.drop_index("ix_index_tri_symbol", table_name="index_tri")
    op.drop_table("index_tri")
    op.drop_index("ix_mf_aaum_period_end", table_name="mf_aaum")
    op.drop_table("mf_aaum")
