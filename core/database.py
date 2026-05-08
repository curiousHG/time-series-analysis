"""SQLModel engine and session factory."""

import logging
import os

from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine

logger = logging.getLogger("data.store.database")

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://harshit@localhost:5432/trading")

engine = create_engine(DATABASE_URL, pool_size=5, max_overflow=2, echo=False)


def get_session() -> Session:
    return Session(engine)


def init_schema():
    """Create all tables from SQLModel models, then run lightweight migrations."""
    import core.models  # noqa: F401 — registers models with SQLModel metadata

    SQLModel.metadata.create_all(engine)
    _run_migrations()
    logger.info("Database schema initialized")


def _run_migrations():
    """Hand-rolled migrations for column additions that create_all() can't perform on existing tables."""
    from mutual_funds.display import short_scheme_name

    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE mf_registry ADD COLUMN IF NOT EXISTS short_name TEXT"))
        rows = conn.execute(text("SELECT scheme_name FROM mf_registry WHERE short_name IS NULL")).all()
        if rows:
            for row in rows:
                conn.execute(
                    text("UPDATE mf_registry SET short_name = :s WHERE scheme_name = :n"),
                    {"s": short_scheme_name(row.scheme_name), "n": row.scheme_name},
                )
            logger.info("Backfilled short_name for %d registry rows", len(rows))
