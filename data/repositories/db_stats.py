"""Database size statistics from the PostgreSQL catalogs."""

from __future__ import annotations

from sqlalchemy import Row, text

from core.database import engine

_TABLES_SQL = text("""
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

_DB_SQL = text("""
    SELECT
        current_database() AS db,
        pg_database_size(current_database()) AS bytes,
        pg_size_pretty(pg_database_size(current_database())) AS pretty
""")


def get_database_size() -> Row:
    """One row: db (name), bytes, pretty."""
    with engine.connect() as conn:
        return conn.execute(_DB_SQL).one()


def load_table_sizes() -> list[Row]:
    """Rows of name, rows, total_bytes, total_pretty for every public table, largest first."""
    with engine.connect() as conn:
        return list(conn.execute(_TABLES_SQL).all())
