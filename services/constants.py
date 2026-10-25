"""Services-package constants: risk-free rates, freshness windows, fetch concurrency, SQL."""

from __future__ import annotations

from typing import Literal

from sqlalchemy import text

from core.constants import TRADING_DAYS

BackfillSource = Literal["nav", "metadata", "holdings"]
SourceStatus = Literal["available", "pending", "unavailable"]
FreshnessStatus = Literal["fresh", "stale", "missing"]
FetchOutcome = Literal["updated", "skipped", "failed"]

BACKTEST_RISK_FREE = 0.065
BACKTEST_RF_DAILY = BACKTEST_RISK_FREE / TRADING_DAYS

RISK_FREE_ANNUAL = 0.06
RF_DAILY = RISK_FREE_ANNUAL / TRADING_DAYS

NAV_STALE_BUSINESS_DAYS = 1
HOLDINGS_STALE_DAYS = 35
DORMANT_AFTER_DAYS = 90

NAV_FETCH_WORKERS = 4
HOLDINGS_FETCH_WORKERS = 16

TABLES_SQL = text("""
    SELECT
        c.relname AS name,
        COALESCE(s.n_live_tup, 0) AS rows,
        pg_total_relation_size(c.oid) AS total_bytes,
        pg_size_pretty(pg_total_relation_size(c.oid)) AS total_pretty
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid
    WHERE c.relkind = 'r' AND n.nspname = 'public'
    ORDER BY total_bytes DESC
""")

DB_SQL = text("""
    SELECT
        current_database() AS db,
        pg_database_size(current_database()) AS bytes,
        pg_size_pretty(pg_database_size(current_database())) AS pretty
""")
