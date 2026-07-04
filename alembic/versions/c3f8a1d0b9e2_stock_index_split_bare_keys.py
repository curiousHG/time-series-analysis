"""Split indices into index_ohlcv/index_registry; bare-key stock_ohlcv; add OHLCV status.

Canonicalises the stock subsystem: equities key by the bare NSE symbol (RELIANCE), indices
move to their own tables (no ISIN, thinner data). Adds availability watermark columns to
stock_registry and seeds the backward-gap floor so the ~30-min cold-load hang can't recur.

Revision ID: c3f8a1d0b9e2
Revises: 8c4d1e2f9a7b
Create Date: 2026-07-04 00:00:00.000000+00:00
"""

from __future__ import annotations

from datetime import date, timedelta

import sqlalchemy as sa

from alembic import op

revision = "c3f8a1d0b9e2"
down_revision = "8c4d1e2f9a7b"
branch_labels = None
depends_on = None

# Rows in stock_ohlcv that are non-equity (indices + FX) — mirrors stocks.constants.is_index_symbol.
_INDEX_WHERE = (
    "symbol LIKE '^%' OR symbol LIKE 'NIFTY %' OR symbol LIKE '%=%' "
    "OR symbol IN ('NIFTY_MIDCAP_100.NS','NIFTY_MIDCAP_150.NS','NIFTY_SMLCAP_250.NS')"
)


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())

    # 1. stock_registry OHLCV availability columns (bare key now aligns with stock_ohlcv).
    have = {c["name"] for c in insp.get_columns("stock_registry")}
    for name, coltype in (
        ("ohlcv_status", sa.String()),
        ("ohlcv_as_of", sa.DateTime()),
        ("ohlcv_earliest", sa.Date()),
        ("ohlcv_empty_streak", sa.Integer()),
    ):
        if name not in have:
            op.add_column("stock_registry", sa.Column(name, coltype, nullable=True))

    tables = set(insp.get_table_names())

    # 2. Dedicated index tables.
    if "index_ohlcv" not in tables:
        op.create_table(
            "index_ohlcv",
            sa.Column("date", sa.Date(), primary_key=True, nullable=False),
            sa.Column("symbol", sa.String(), primary_key=True, nullable=False),
            sa.Column("open", sa.Float()),
            sa.Column("high", sa.Float()),
            sa.Column("low", sa.Float()),
            sa.Column("close", sa.Float()),
            sa.Column("volume", sa.BigInteger()),
        )
        op.create_index("ix_index_ohlcv_symbol", "index_ohlcv", ["symbol"])
    if "index_registry" not in tables:
        op.create_table(
            "index_registry",
            sa.Column("symbol", sa.String(), primary_key=True, nullable=False),
            sa.Column("name", sa.String()),
            sa.Column("status", sa.String()),
            sa.Column("as_of", sa.DateTime()),
            sa.Column("earliest", sa.Date()),
        )

    # 3. Move index rows out of stock_ohlcv → index_ohlcv.
    op.execute(
        f"INSERT INTO index_ohlcv (date, symbol, open, high, low, close, volume) "
        f"SELECT date, symbol, open, high, low, close, volume FROM stock_ohlcv WHERE {_INDEX_WHERE} "
        f"ON CONFLICT (date, symbol) DO NOTHING"
    )
    op.execute(f"DELETE FROM stock_ohlcv WHERE {_INDEX_WHERE}")

    # 4. Rewrite remaining equity `.NS` rows to the bare symbol (skip any that would collide).
    op.execute(
        "UPDATE stock_ohlcv o SET symbol = left(o.symbol, length(o.symbol) - 3) "
        "WHERE o.symbol LIKE '%.NS' "
        "AND NOT EXISTS (SELECT 1 FROM stock_ohlcv b "
        "WHERE b.symbol = left(o.symbol, length(o.symbol) - 3) AND b.date = o.date)"
    )

    # 5. Seed index_registry earliest floor + status from moved data.
    op.execute(
        "INSERT INTO index_registry (symbol, earliest, status) "
        "SELECT symbol, min(date), 'available' FROM index_ohlcv GROUP BY symbol "
        "ON CONFLICT (symbol) DO UPDATE SET earliest = EXCLUDED.earliest, status = 'available'"
    )

    # 6. Seed stock_registry status + earliest floor. Only lock the floor for symbols with
    #    genuinely-old history (min(date) < today-3y) so narrow (500-800d) fetches aren't frozen
    #    at a false floor; younger ones self-heal on their next full load.
    op.execute(
        sa.text(
            "UPDATE stock_registry r SET ohlcv_status='available', ohlcv_empty_streak=0, ohlcv_as_of=now(), "
            "ohlcv_earliest = CASE WHEN s.mn < :cut THEN s.mn ELSE NULL END "
            "FROM (SELECT symbol, min(date) mn FROM stock_ohlcv GROUP BY symbol) s "
            "WHERE r.symbol = s.symbol"
        ).bindparams(cut=date.today() - timedelta(days=1095))
    )


def downgrade() -> None:
    # Re-suffixing equities / merging indices back is ambiguous; only drop the additive schema.
    insp = sa.inspect(op.get_bind())
    have = {c["name"] for c in insp.get_columns("stock_registry")}
    for name in ("ohlcv_empty_streak", "ohlcv_earliest", "ohlcv_as_of", "ohlcv_status"):
        if name in have:
            op.drop_column("stock_registry", name)
    tables = set(insp.get_table_names())
    if "index_registry" in tables:
        op.drop_table("index_registry")
    if "index_ohlcv" in tables:
        op.drop_table("index_ohlcv")
