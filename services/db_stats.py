"""Database statistics — sizes, table counts, row counts (PostgreSQL only)."""

from dataclasses import dataclass

from data.repositories.db_stats import get_database_size, load_table_sizes


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


def get_db_stats() -> DbStats:
    db_row = get_database_size()
    table_rows = load_table_sizes()

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
