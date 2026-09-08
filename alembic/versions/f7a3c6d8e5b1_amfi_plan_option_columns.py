"""AMFI plan/option columns + category dim repair.

AMFI's NAVAll.txt moved the plan/option suffix out of the scheme name into dedicated `Plan` and
`Option` columns. `amfi_schemes` now stores both verbatim so the screener can filter on the
source's own values instead of regex-guessing them from the name.

The category dim is repaired in the same step: the old header parser produced near-duplicate
asset classes ("Equity" vs "Equity s" from the plural "Equity Schemes" header, a double-spaced
"Fund of Funds  (Domestic)"), which surfaced as separate options in the screener's Category
filter. Duplicates are merged into the canonical name and unreferenced dim rows dropped.

Revision ID: f7a3c6d8e5b1
Revises: e2f5c8b1a0d3
Create Date: 2026-09-04 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "f7a3c6d8e5b1"
down_revision = "e2f5c8b1a0d3"
branch_labels = None
depends_on = None

# (duplicate name produced by the old parser, canonical name it should have been)
_CATEGORY_MERGES = (
    ("Equity s", "Equity"),
    ("Debt s", "Debt"),
    ("Hybrid s", "Hybrid"),
    ("Solution Oriented s", "Solution Oriented"),
    ("Fund of Funds  (Domestic)", "Fund of Funds"),
    ("Fund of Funds (Domestic)", "Fund of Funds"),
    ("Exchange Traded Funds (ETFs)", "ETF"),
    ("Overseas Fund of Funds", "Fund of Funds"),
    ("Index Funds", "Index"),
    ("Children’s Fund", "Solution Oriented"),  # noqa: RUF001 — the curly form AMFI actually emits
    ("Children's Fund", "Solution Oriented"),
    ("Childrens Fund", "Solution Oriented"),
    ("Life Cycle Funds", "Solution Oriented"),
)


def upgrade() -> None:
    op.add_column("amfi_schemes", sa.Column("plan", sa.String(), nullable=True))
    op.add_column("amfi_schemes", sa.Column("option", sa.String(), nullable=True))
    op.create_index("ix_amfi_schemes_plan", "amfi_schemes", ["plan"])
    op.create_index("ix_amfi_schemes_option", "amfi_schemes", ["option"])

    conn = op.get_bind()
    for duplicate, canonical in _CATEGORY_MERGES:
        canonical_id = conn.execute(sa.text("SELECT id FROM mf_category WHERE name = :n"), {"n": canonical}).scalar()
        duplicate_id = conn.execute(sa.text("SELECT id FROM mf_category WHERE name = :n"), {"n": duplicate}).scalar()
        if duplicate_id is None:
            continue
        if canonical_id is None:
            conn.execute(
                sa.text("UPDATE mf_category SET name = :n WHERE id = :i"),
                {"n": canonical, "i": duplicate_id},
            )
            continue
        for table in ("amfi_schemes", "mf_metadata"):
            conn.execute(
                sa.text(f"UPDATE {table} SET category_id = :c WHERE category_id = :d"),
                {"c": canonical_id, "d": duplicate_id},
            )
        conn.execute(sa.text("DELETE FROM mf_category WHERE id = :i"), {"i": duplicate_id})

    # Dim rows left behind by earlier classification schemes ("Equity: Large Cap", "Debt: Liquid", …).
    conn.execute(
        sa.text(
            "DELETE FROM mf_category c WHERE NOT EXISTS "
            "(SELECT 1 FROM amfi_schemes s WHERE s.category_id = c.id) AND NOT EXISTS "
            "(SELECT 1 FROM mf_metadata m WHERE m.category_id = c.id)"
        )
    )


def downgrade() -> None:
    op.drop_index("ix_amfi_schemes_option", table_name="amfi_schemes")
    op.drop_index("ix_amfi_schemes_plan", table_name="amfi_schemes")
    op.drop_column("amfi_schemes", "option")
    op.drop_column("amfi_schemes", "plan")
    # Category merges are not reversed — the duplicate names carried no information the
    # canonical rows don't, and the pruned rows had no schemes pointing at them.
