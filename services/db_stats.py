"""Database statistics — sizes, table counts, row counts (PostgreSQL only)."""

from dataclasses import dataclass

from sqlalchemy import text

from core.database import engine


@dataclass
class TableStat:
    name: str
    rows: int
    total_bytes: int
    total_pretty: str


@dataclass
class DbStats:
    db_name: str
    db_bytes: int
    db_pretty: str
    table_count: int
    tables: list[TableStat]


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


def get_db_stats() -> DbStats:
    with engine.connect() as conn:
        db_row = conn.execute(_DB_SQL).one()
        table_rows = conn.execute(_TABLES_SQL).all()

    tables = [
        TableStat(
            name=r.name,
            rows=int(r.rows),
            total_bytes=int(r.total_bytes),
            total_pretty=r.total_pretty,
        )
        for r in table_rows
    ]

    return DbStats(
        db_name=db_row.db,
        db_bytes=int(db_row.bytes),
        db_pretty=db_row.pretty,
        table_count=len(tables),
        tables=tables,
    )
