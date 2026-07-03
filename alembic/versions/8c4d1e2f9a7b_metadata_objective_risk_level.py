"""mf_metadata: investment objective + SEBI riskometer level

Revision ID: 8c4d1e2f9a7b
Revises: 5a2c9d4e1b3f
Create Date: 2026-07-03 00:00:00.000000
"""

from __future__ import annotations

from alembic import op

revision = "8c4d1e2f9a7b"
down_revision = "5a2c9d4e1b3f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE mf_metadata ADD COLUMN IF NOT EXISTS investment_objective TEXT")
    op.execute("ALTER TABLE mf_metadata ADD COLUMN IF NOT EXISTS risk_level VARCHAR")


def downgrade() -> None:
    op.execute("ALTER TABLE mf_metadata DROP COLUMN IF EXISTS risk_level")
    op.execute("ALTER TABLE mf_metadata DROP COLUMN IF EXISTS investment_objective")
