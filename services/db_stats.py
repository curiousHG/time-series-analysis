"""Database statistics — sizes, table counts, row counts (PostgreSQL only)."""

from dataclasses import dataclass

from core.database import engine
from services.constants import DB_SQL, TABLES_SQL


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
    with engine.connect() as conn:
        db_row = conn.execute(DB_SQL).one()
        table_rows = conn.execute(TABLES_SQL).all()

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
