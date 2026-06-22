"""add amfi local db added timestamp

Revision ID: 20260613_0001
Revises:
Create Date: 2026-06-13 00:00:00.000000
"""

from __future__ import annotations

from alembic import op

revision = "20260613_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE amfi_schemes ADD COLUMN IF NOT EXISTS db_added_at TIMESTAMP")
    op.execute("CREATE INDEX IF NOT EXISTS idx_amfi_schemes_db_added_at ON amfi_schemes (db_added_at DESC)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_amfi_schemes_db_added_at")
    op.execute("ALTER TABLE amfi_schemes DROP COLUMN IF EXISTS db_added_at")
