"""SQLModel engine and session factory."""

import logging
import os

from sqlmodel import Session, SQLModel, create_engine

logger = logging.getLogger("data.store.database")

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://harshit@localhost:5432/trading")

engine = create_engine(DATABASE_URL, pool_size=5, max_overflow=2, echo=False)


def get_session() -> Session:
    return Session(engine)


def init_schema() -> None:
    """Create any missing tables on startup. Migrations are NOT run here — they live in
    the `migrations` package and must be invoked manually after model changes via
    `scripts/migrate.py` (see CLAUDE.md).

    Why: migrations include heavy operations (ALTER COLUMN TYPE, GIN index builds) that
    can take >2 minutes. `SQLModel.metadata.create_all` alone is idempotent and fast
    (~60ms) — safe to call on every Streamlit boot.
    """
    from core.timing import timed

    with timed("init_schema.import_models"):
        import core.models  # noqa: F401 — registers models with SQLModel metadata

    with timed("init_schema.create_all"):
        SQLModel.metadata.create_all(engine)
    logger.info("Database schema initialized (run scripts/migrate.py for schema migrations)")
