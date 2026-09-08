"""Persist the AdvisorKhoj slug that resolved for a tracked fund.

AdvisorKhoj keys funds by AMFI's pre-2025 scheme names, which can no longer be derived from
the current feed with certainty. The holdings resolver probes a handful of spellings and
verifies the returned scheme against ours; once a spelling works it is stored here so every
later refresh is a single request.

Revision ID: g8b4d7e9f2c3
Revises: f7a3c6d8e5b1
Create Date: 2026-09-04 02:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "g8b4d7e9f2c3"
down_revision = "f7a3c6d8e5b1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("mf_registry", sa.Column("advisorkhoj_slug", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("mf_registry", "advisorkhoj_slug")
