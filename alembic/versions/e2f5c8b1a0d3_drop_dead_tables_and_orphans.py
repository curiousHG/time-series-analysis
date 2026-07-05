"""drop 5 dead/superseded empty tables + purge a 1-row orphan index key

Cleanup (purge junk, keep coexistence):
- DROP the five model-less, zero-row, zero-FK tables that lingered from earlier designs:
  `stock_ohlcv_status` and `scheme_code_map` are superseded (by `stock_registry.ohlcv_*` columns and
  `amfi_schemes` respectively); `bots`/`orders`/`trades` are an unused trading-bot schema never wired up.
- DELETE the 1-row orphan `NIFTY_MIDCAP_150.NS` index key (delisted on yfinance, a single 2020 row, not
  referenced by benchmark routing — which uses the `NIFTY MIDCAP 150` space-form).

The `^`/NSE-name/ALL-CAPS index-key coexistence is intentionally LEFT in place: the ALL-CAPS space-forms
and `NIFTY_MIDCAP_100.NS` are fresh and actively used by benchmark resolution, and `is_index_symbol`
recognizes them but not the title-case bhavcopy names — canonicalizing would require redirecting
benchmarks + teaching `is_index_symbol` the bhavcopy names, which is deferred.

Revision ID: e2f5c8b1a0d3
Revises: d1e4b7c2a9f8
Create Date: 2026-07-05 00:00:00.000000
"""

from __future__ import annotations

from alembic import op

revision = "e2f5c8b1a0d3"
down_revision = "d1e4b7c2a9f8"
branch_labels = None
depends_on = None

_DEAD_TABLES = ("stock_ohlcv_status", "scheme_code_map", "bots", "orders", "trades")


def upgrade() -> None:
    for table in _DEAD_TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table}")
    op.execute("DELETE FROM index_ohlcv WHERE symbol = 'NIFTY_MIDCAP_150.NS'")
    op.execute("DELETE FROM index_registry WHERE symbol = 'NIFTY_MIDCAP_150.NS'")


def downgrade() -> None:
    # Irreversible cleanup — the dropped tables were empty/unused and the orphan row carried no
    # information. Nothing to restore.
    pass
