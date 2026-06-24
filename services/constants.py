"""Services-package constants: risk-free rates, freshness windows, fetch concurrency, SQL."""

from __future__ import annotations

from sqlalchemy import text

# Annual risk-free rate used by the strategy backtest (Indian markets).
BACKTEST_RISK_FREE = 0.065

# NAV-derived MF risk/return metrics (services.mf_metrics).
RISK_FREE_ANNUAL = 0.06
RF_DAILY = RISK_FREE_ANNUAL / 252
TRADING_DAYS = 252

# Data-freshness thresholds (services.data_freshness).
NAV_STALE_BUSINESS_DAYS = 1
HOLDINGS_STALE_DAYS = 35

# Parallel fetch pool sizes (services.sync_service). Empirically tuned: MFAPI handles
# 16-way concurrency cleanly (~55 schemes/s, p95 ~325ms); AdvisorKhoj ~78 rps p95 ~190ms.
NAV_FETCH_WORKERS = 16
HOLDINGS_FETCH_WORKERS = 16

# PostgreSQL stats queries (services.db_stats).
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
